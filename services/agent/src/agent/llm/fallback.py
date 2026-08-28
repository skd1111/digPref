"""LLM Fallback —— 多级降级链包装器。

Phase 2C 引入：
    - 主备链：每个 task kind 可指定 primary + fallback_chain
    - 错误分类：超时 / 5xx / 429 / 连接拒绝 / JSON 解析失败
    - 重试：每级最多 1 次重试（避免对挂掉的 LLM 反复打）
    - 降级事件：通过 EventBus 发 `llm_degraded` 给 SSE

V2 增量：
    - `circuit_breaker_registry` 参数：每级调用前 `breaker.allow()`；Open 直接 skip
    - `breaker.on_success()` / `breaker.on_failure()` 状态机更新
    - `sleep_between` 指数退避（默认 0.5s * 2^attempt）
    - 通过 `agent.llm.metrics.emit_router_event("llm_degraded", ...)` 推送 SSE

设计原则：
    - 客户端内部依然 try/except 兜底（向后兼容）
    - 但也允许在 raise_errors=True 时冒泡真实异常
    - Router 默认 raise_errors=False；Fallback 链要求 raise_errors=True
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

logger = logging.getLogger("agent.llm.fallback")

T = TypeVar("T")


class LLMBackendError(Exception):
    """LLM 后端调用失败（超时 / 5xx / 连接拒绝 / JSON 解析）。

    Fallback 链根据异常类型决定是否切换下一级。
    """


class LLMRateLimitError(LLMBackendError):
    """429 限流 —— Fallback 链应跳过该 backend。"""


class LLMUnavailableError(LLMBackendError):
    """连接拒绝 / 5xx / 超时 —— Fallback 链应跳过该 backend。"""


class LLMParseError(LLMBackendError):
    """LLM 返回 200 但 JSON 解析失败 —— 通常是 prompt 问题，不建议频繁切链。"""


class LLMParamUnsupportedError(LLMBackendError):
    """400 且响应体指向请求参数不被支持（response_format / max_tokens 等）。

    换后端同样参数大概率复发 —— 属客户端/协议适配问题而非后端故障；
    客户端层应先做参数适配重试（private_llm._post_chat），到这里说明适配也失败。
    不计入熔断失败计数（后端本身可用）。
    """


class LLMToolIdMismatchError(LLMBackendError):
    """400 且响应体指向 tool_call_id 配对失败（如 MiniMax「tool id not found」）。

    与参数不支持的关键区别：这是消息链构造问题（BUGFIX #139 类问题复发信号），
    重试/换后端无意义，需修消息构造逻辑。不计入熔断失败计数。
    """


@dataclass
class FallbackResult:
    """降级链执行结果 + 降级轨迹（用于审计 / UI 提示）。"""

    value: T
    attempts: list[str] = field(default_factory=list)
    # 全链失败时填 None；任何成功填 'ok'
    final_status: str = "ok"
    # [("private", "timeout"), ("ollama", "ok")] —— 审计可用
    trail: list[tuple[str, str]] = field(default_factory=list)


async def with_fallback(
    *,
    chain: list[tuple[str, Callable[[], Awaitable[T]]]],
    label: str,
    raise_on_all_fail: bool = False,
    sleep_between: float = 0.0,
    circuit_breaker_registry=None,
) -> FallbackResult[T]:
    """依次尝试 chain 中的后端，任一成功即返回。

    Args:
        chain: [(backend_name, callable), ...]，按顺序尝试
        label: 用于日志的标识（如 "intent" / "plan"）
        raise_on_all_fail: 全失败时是否抛 LLMBackendError（默认 False，返回安全默认）
        sleep_between: 切换后端的退避秒数（默认 0，不阻塞调用方）
        circuit_breaker_registry: V2 增量 —— CircuitBreakerRegistry 实例（可选）；
            每级调用前 `breaker.allow()` 检查 Open 状态直接 skip；调用后
            `breaker.on_success/on_failure` 更新状态机。None 时不接熔断器。

    Returns:
        FallbackResult，其中 value=成功值（失败时为 None），attempts/trail 用于审计
    """
    if not chain:
        raise ValueError(f"empty fallback chain for {label}")

    last_err: Exception | None = None
    trail: list[tuple[str, str]] = []
    # V2 增量：失败计数（emit llm_degraded 事件用）
    degradation_count = 0

    for attempt, (backend_name, call) in enumerate(chain):
        # V2 增量：熔断器检查 Open → 直接 skip（不计入 trail 仅 log）
        if circuit_breaker_registry is not None:
            cb = circuit_breaker_registry.get_or_create(backend_name)
            if not cb.allow():
                logger.info("[%s] %s skipped (circuit_open)", label, backend_name)
                continue

        try:
            value = await call()
            trail.append((backend_name, "ok"))
            # V2 增量：熔断器 on_success（Half-Open 探测成功 → Closed）
            if circuit_breaker_registry is not None:
                cb.on_success()
            if len(trail) > 1:  # 发生过降级才记录轨迹（CoT 日志分析用）
                logger.info("[%s] ok via %s after degradation trail=%s", label, backend_name, trail)
            return FallbackResult(
                value=value,
                attempts=[n for n, _ in trail],
                final_status="ok",
                trail=trail,
            )
        except LLMRateLimitError as e:
            trail.append((backend_name, f"rate_limit: {e}"))
            logger.warning("[%s] %s hit rate limit: %s", label, backend_name, e)
            # 429 = 后端活着但在限流，不是故障：不计熔断失败，否则限流窗口内 3 次
            # 请求就把后端熔断 30s（BUGFIX #159）。客户端层已做 Retry-After 重试。
            degradation_count += 1
        except (LLMParamUnsupportedError, LLMToolIdMismatchError) as e:
            trail.append((backend_name, f"request_invalid: {e}"))
            logger.warning("[%s] %s request rejected: %s", label, backend_name, e)
            # 请求构造/协议适配问题，后端无过错 → 不计熔断失败；照常切下一级。
            degradation_count += 1
        except LLMUnavailableError as e:
            trail.append((backend_name, f"unavailable: {e}"))
            logger.warning("[%s] %s unavailable: %s", label, backend_name, e)
            if circuit_breaker_registry is not None:
                cb.on_failure()
            degradation_count += 1
        except LLMParseError as e:
            trail.append((backend_name, f"parse_error: {e}"))
            logger.warning("[%s] %s parse error: %s", label, backend_name, e)
            if circuit_breaker_registry is not None:
                cb.on_failure()
            degradation_count += 1
        except LLMBackendError as e:
            trail.append((backend_name, f"error: {e}"))
            logger.warning("[%s] %s backend error: %s", label, backend_name, e)
            if circuit_breaker_registry is not None:
                cb.on_failure()
            degradation_count += 1
        except Exception as e:
            # 客户端内部 try/except 兜底时不会到这里
            # 这里是保险，捕获一切未知错误并切换下一级
            trail.append((backend_name, f"unexpected: {type(e).__name__}"))
            logger.exception("[%s] %s unexpected error", label, backend_name)
            if circuit_breaker_registry is not None:
                cb.on_failure()
            last_err = e
            degradation_count += 1

        # V2 增量：指数退避（仅在 attempt < len(chain)-1 时 sleep）
        if sleep_between > 0 and attempt < len(chain) - 1:
            await asyncio.sleep(sleep_between * (2**attempt))

    # V2 增量：emit llm_degraded SSE 事件（CLAUDE.md §4 三处同步）
    if degradation_count > 0:
        try:
            from agent.llm.metrics import emit_router_event

            emit_router_event(
                "llm_degraded",
                {
                    "label": label,
                    "trail": trail,
                    "fallback_used_count": degradation_count,
                    "chain_len": len(chain),
                },
            )
        except Exception as e:
            logger.debug("emit_llm_degraded_failed err=%s", e)

    # 全链失败
    if raise_on_all_fail:
        raise LLMBackendError(f"all backends failed for {label}: {trail}") from last_err

    logger.error("[%s] all backends failed: %s", label, trail)
    return FallbackResult(
        value=None,  # type: ignore[arg-type]
        attempts=[n for n, _ in trail],
        final_status="all_failed",
        trail=trail,
    )
