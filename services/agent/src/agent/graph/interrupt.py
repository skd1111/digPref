"""HITL 暂停/恢复原语 —— 用 Redis 做协调，后端无阻塞设计。

设计原则（借鉴 OpenAI Codex CLI 的审批模式）：
    - 审批请求立即返回（非阻塞），让 SSE 适配器有时间发出审批事件
    - 后台轮询 Redis 等待决策，决策到达后写入内存供下次节点访问
    - Redis 不可用时自动降级到进程内 dict（单机本地开发可用）

生产环境依赖 Redis：
    - Tauri 桌面端和 Agent 经常不在同一台机器上
    - 同一笔审批可能有多个 Agent 副本在抢；Redis 保证唯一性
    - Agent 重启也能恢复：任意接住 LangGraph thread 的副本都能继续
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

log = logging.getLogger(__name__)


# ---- 公开 API ----------------------------------------------------------------


async def start_approval(
    *,
    approval_id: str,
    plan: dict,
    timeout_sec: int,
) -> None:
    """发起审批请求（非阻塞）。

    将待审批计划写入 Redis（或内存 fallback），然后立即返回。
    调用方应随后将 awaiting_approval=True 写入状态更新，
    让 SSE 适配器向前端发出审批事件。
    """
    await _store_pending(approval_id, plan)
    # 启动后台轮询任务，决策到达后写入 _decisions 内存缓存
    # 使用 create_task + add_done_callback 捕获异常，
    # 避免 ensure_future 静默吞掉后台任务错误
    task = asyncio.create_task(_poll_until_decision(approval_id, timeout_sec))
    task.add_done_callback(
        lambda t: log.error("审批轮询任务异常: %s", t.exception()) if t.exception() else None
    )


async def check_decision(approval_id: str) -> str | None:
    """检查审批决策是否已到达。

    返回 "approve"、"reject" 或 None（尚未决定）。
    此函数由 hitl_gate 在后续迭代中调用。
    """
    # 先检查内存缓存（由后台轮询任务写入）
    local = _LOCAL_DECISIONS.pop(approval_id, None)
    if local is not None:
        return local
    # 回退到直接读 Redis（处理 Agent 重启场景）
    return await _read_decision(approval_id)


async def post_decision(approval_id: str, decision: str) -> None:
    """由 /approval 端点调用 —— 写入决策供后台轮询任务消费。"""
    if decision not in ("approve", "reject"):
        raise ValueError(f"无效的决策值: {decision!r}")
    await _store_decision(approval_id, decision)
    log.info("HITL 决策已存储: %s -> %s", approval_id, decision)


async def cleanup_approval(approval_id: str) -> None:
    """清理审批相关的临时数据。由 hitl_gate 在决策处理后调用。"""
    await _cleanup(approval_id)


# ---- 后台轮询 ----------------------------------------------------------------


async def _poll_until_decision(approval_id: str, timeout_sec: int) -> None:
    """后台任务：轮询 Redis 直到决策到达或超时。

    决策到达后写入内存缓存 _LOCAL_DECISIONS，供 check_decision() 取用。
    超时后自动写入 "reject"。
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    poll_interval = 0.25

    while asyncio.get_event_loop().time() < deadline:
        decision = await _read_decision(approval_id)
        if decision:
            _LOCAL_DECISIONS[approval_id] = decision
            return
        await asyncio.sleep(poll_interval)

    # 超时 → 自动拒绝
    log.warning("审批 %s 超时（%ds），自动拒绝", approval_id, timeout_sec)
    _LOCAL_DECISIONS[approval_id] = "reject"


# ---- Redis 后端（优先）-------------------------------------------------------


async def _store_pending(approval_id: str, plan: dict) -> None:
    redis = _redis()
    if redis is None:
        return
    try:
        await redis.set(
            _pending_key(approval_id),
            json.dumps(plan, default=str),
            ex=3600,
        )
    except Exception as exc:
        log.warning("Redis 存储待审批计划失败: %s", exc)


async def _read_decision(approval_id: str) -> str | None:
    redis = _redis()
    if redis is None:
        return _LOCAL_DECISIONS.pop(approval_id, None)
    try:
        raw = await redis.get(_decision_key(approval_id))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw
    except Exception as exc:
        log.warning("Redis 读取决策失败: %s", exc)
        return _LOCAL_DECISIONS.pop(approval_id, None)


async def _store_decision(approval_id: str, decision: str) -> None:
    redis = _redis()
    if redis is None:
        _LOCAL_DECISIONS[approval_id] = decision
        return
    try:
        await redis.set(_decision_key(approval_id), decision, ex=3600)
    except Exception as exc:
        log.warning("Redis 存储决策失败，降级到内存: %s", exc)
        _LOCAL_DECISIONS[approval_id] = decision


async def _cleanup(approval_id: str) -> None:
    redis = _redis()
    if redis is None:
        return
    try:
        await redis.delete(_pending_key(approval_id), _decision_key(approval_id))
    except Exception:
        pass


# ---- 内存 fallback（仅开发环境）----------------------------------------------

_LOCAL_DECISIONS: dict[str, str] = {}

# ---- Redis 客户端（延迟初始化）------------------------------------------------

_REDIS = None


def _redis_url() -> str | None:
    return os.environ.get("EAIDE_REDIS_URL")


def _redis():
    """返回缓存的异步 Redis 客户端，未配置时返回 None。"""
    global _REDIS
    if _REDIS is not None:
        return _REDIS
    url = _redis_url()
    if not url:
        return None
    try:
        from redis.asyncio import Redis

        _REDIS = Redis.from_url(url, decode_responses=False)
    except Exception as exc:
        log.warning("Redis 不可用，降级到进程内事件: %s", exc)
        _REDIS = None
    return _REDIS


def _pending_key(approval_id: str) -> str:
    return f"eaide:approval:pending:{approval_id}"


def _decision_key(approval_id: str) -> str:
    return f"eaide:approval:decision:{approval_id}"
