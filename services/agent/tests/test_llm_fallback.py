"""Phase 2C: LLM Fallback 链测试。

覆盖：
    - 正常：primary 成功 → trail 仅一项 ok
    - 降级：primary 抛 LLMUnavailableError → 自动切 secondary
    - 限流：primary 抛 LLMRateLimitError → 切 secondary
    - 全失败：所有后端抛 → 返回 final_status='all_failed' 且 raise=False 不抛
    - raise_on_all_fail=True 时全链失败抛 LLMBackendError
    - FallbackResult 包含完整 trail
"""
from __future__ import annotations

import pytest

from agent.llm.fallback import (
    FallbackResult,
    LLMBackendError,
    LLMRateLimitError,
    LLMUnavailableError,
    with_fallback,
)


# ---------- 正常路径 ----------------------------------------------------------


async def test_primary_success_no_fallback():
    """primary 成功 → 不调用 secondary，trail 只有一项。"""
    async def primary():
        return "hello"

    async def secondary():
        raise AssertionError("should not be called")

    result = await with_fallback(
        chain=[("primary", primary), ("secondary", secondary)],
        label="test",
    )

    assert result.final_status == "ok"
    assert result.value == "hello"
    assert result.trail == [("primary", "ok")]
    assert result.attempts == ["primary"]


# ---------- 降级路径 ----------------------------------------------------------


async def test_fallback_to_secondary_on_unavailable():
    """primary 抛 LLMUnavailableError → 自动切 secondary。"""
    async def primary():
        raise LLMUnavailableError("connection refused")

    async def secondary():
        return "fallback_ok"

    result = await with_fallback(
        chain=[("primary", primary), ("secondary", secondary)],
        label="test",
    )

    assert result.final_status == "ok"
    assert result.value == "fallback_ok"
    assert result.trail == [
        ("primary", "unavailable: connection refused"),
        ("secondary", "ok"),
    ]


async def test_fallback_to_secondary_on_rate_limit():
    """primary 抛 LLMRateLimitError → 切 secondary。"""
    async def primary():
        raise LLMRateLimitError("429 too many requests")

    async def secondary():
        return "via_b"

    result = await with_fallback(
        chain=[("primary", primary), ("secondary", secondary)],
        label="intent",
    )

    assert result.final_status == "ok"
    assert result.value == "via_b"
    assert result.attempts == ["primary", "secondary"]


async def test_fallback_three_levels():
    """三级链：primary 挂 → secondary 挂 → tertiary 成功。"""
    async def primary():
        raise LLMUnavailableError("p dead")

    async def secondary():
        raise LLMRateLimitError("s 429")

    async def tertiary():
        return "t ok"

    result = await with_fallback(
        chain=[("p", primary), ("s", secondary), ("t", tertiary)],
        label="plan",
    )

    assert result.final_status == "ok"
    assert result.value == "t ok"
    assert len(result.trail) == 3
    assert result.trail[-1] == ("t", "ok")


async def test_unknown_exception_also_triggers_fallback():
    """未知异常（不是 LLMBackendError 子类）也应触发降级。"""
    async def primary():
        raise RuntimeError("something weird")

    async def secondary():
        return "ok"

    result = await with_fallback(
        chain=[("p", primary), ("s", secondary)],
        label="test",
    )

    assert result.final_status == "ok"
    assert result.value == "ok"
    assert "unexpected: RuntimeError" in result.trail[0][1]


# ---------- 全失败 ------------------------------------------------------------


async def test_all_failed_default_returns_none():
    """默认 raise_on_all_fail=False → 返回 value=None, final_status='all_failed'。"""
    async def primary():
        raise LLMUnavailableError("p dead")

    async def secondary():
        raise LLMUnavailableError("s dead")

    result = await with_fallback(
        chain=[("p", primary), ("s", secondary)],
        label="test",
        raise_on_all_fail=False,
    )

    assert result.final_status == "all_failed"
    assert result.value is None
    assert len(result.trail) == 2
    assert all("unavailable" in t[1] for t in result.trail)


async def test_all_failed_raise_on_all_fail_true():
    """raise_on_all_fail=True → 抛 LLMBackendError。"""
    async def primary():
        raise LLMUnavailableError("p dead")

    async def secondary():
        raise LLMUnavailableError("s dead")

    with pytest.raises(LLMBackendError, match="all backends failed"):
        await with_fallback(
            chain=[("p", primary), ("s", secondary)],
            label="intent",
            raise_on_all_fail=True,
        )


async def test_empty_chain_raises_value_error():
    """chain 为空 → ValueError（程序员错误，不静默）。"""
    with pytest.raises(ValueError, match="empty fallback chain"):
        await with_fallback(chain=[], label="empty")


# ---------- 退避 --------------------------------------------------------------


async def test_sleep_between_backoff():
    """sleep_between > 0 → 切换下一级前 sleep。"""
    import time

    async def primary():
        raise LLMUnavailableError("p")

    async def secondary():
        return "ok"

    start = time.monotonic()
    result = await with_fallback(
        chain=[("p", primary), ("s", secondary)],
        label="test",
        sleep_between=0.05,
    )
    elapsed = time.monotonic() - start

    assert result.final_status == "ok"
    # sleep_between 在切链时 sleep，0.05s 是合理上界
    assert 0.04 <= elapsed <= 0.5


# ---------- Router 集成 -------------------------------------------------------


async def test_router_classify_intent_backward_compatible():
    """Router.classify_intent 在 mock 模式下返回字符串（向后兼容）。"""
    from agent.llm.router import LMRouter

    router = LMRouter()
    result = await router.classify_intent("查订单")

    assert isinstance(result, str)
    assert result in ("query", "mutate", "orchestrate", "chitchat")


async def test_router_classify_intent_with_fallback_trail():
    """classify_intent_with_fallback 返回 FallbackResult 含 trail。"""
    from agent.llm.router import LMRouter
    from agent.llm.fallback import FallbackResult

    router = LMRouter()
    result = await router.classify_intent_with_fallback("查订单")

    assert isinstance(result, FallbackResult)
    # conftest 已禁用内网 LLM，链路 = primary → mock
    assert result.final_status == "ok"
    assert len(result.trail) >= 1
    assert result.trail[-1][1] == "ok"