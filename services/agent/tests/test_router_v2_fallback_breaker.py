"""Phase 2C V2 — fallback.py 接 CircuitBreakerRegistry + retry-with-backoff。

覆盖：
  - breaker.allow() 返回 False（Open 状态）→ chain 中该 backend 被 skip（不入 trail）
  - 失败 3 次 → breaker state=Open
  - 成功后 breaker state=Half-Open → Closed
  - sleep_between 指数退避（0.5s * 2^attempt）
"""

import asyncio
import time

from agent.llm.circuit_breaker import CircuitBreakerRegistry
from agent.llm.fallback import (
    LLMBackendError,
    LLMUnavailableError,
    with_fallback,
)


def test_open_breaker_skipped_without_trail_entry():
    """Open 状态的 backend 被 skip（不入 trail，仅 log），节省时间。"""
    registry = CircuitBreakerRegistry(failure_threshold=2, reset_timeout_s=30.0)
    # 手动让 backend "a" Open
    cb_a = registry.get_or_create("a")
    cb_a.on_failure()
    cb_a.on_failure()  # 触发 Open
    assert cb_a.state.value == "open"

    async def call_a():
        raise AssertionError("a should be skipped, not called")

    async def call_b():
        return "ok"

    result = asyncio.run(
        with_fallback(
            chain=[("a", call_a), ("b", call_b)],
            label="test",
            circuit_breaker_registry=registry,
        )
    )
    assert result.final_status == "ok"
    # a 被 skip（不在 trail）；b 成功
    assert ("a", "ok") not in result.trail
    assert ("b", "ok") in result.trail


def test_three_failures_open_breaker():
    """3 次连续失败 → breaker state=Open（threshold=3）。"""
    registry = CircuitBreakerRegistry(failure_threshold=3, reset_timeout_s=30.0)

    async def always_fail():
        raise LLMBackendError("nope")

    # 第一次：3 次失败（同一 backend "a" 走 1 次 + chain 后切换 2 次也 fail）
    async def driver():
        # chain 全是 "a"
        for _ in range(3):
            await with_fallback(
                chain=[("a", always_fail)],
                label="test",
                circuit_breaker_registry=registry,
                raise_on_all_fail=False,
            )

    asyncio.run(driver())
    cb = registry.get_or_create("a")
    assert cb.state.value == "open", f"expected open after 3 failures, got {cb.state.value}"


def test_success_in_half_open_resets_to_closed():
    """Half-Open 探测成功 → Closed（仅当 reset_timeout_s 已过）。"""
    registry = CircuitBreakerRegistry(failure_threshold=2, reset_timeout_s=0.1)

    async def always_fail():
        raise LLMBackendError("nope")

    async def succeed():
        return "ok"

    async def driver():
        # 触发 Open
        cb = registry.get_or_create("a")
        cb.on_failure()
        cb.on_failure()
        assert cb.state.value == "open"
        # 等过 reset_timeout
        await asyncio.sleep(0.15)
        # 此时 cb 应该是 half_open（下次访问 state 时自动转换）
        assert cb.state.value == "half_open"
        # 探测成功 → Closed
        result = await with_fallback(
            chain=[("a", succeed)],
            label="probe",
            circuit_breaker_registry=registry,
        )
        assert result.final_status == "ok"
        assert cb.state.value == "closed", (
            f"expected closed after probe success, got {cb.state.value}"
        )

    asyncio.run(driver())


def test_exponential_backoff_timing():
    """sleep_between > 0 时，attempt N 的实际 sleep = base * 2^N。

    chain 只有 2 项，所以最多 sleep 1 次（attempt 0）。
    """
    registry = CircuitBreakerRegistry(failure_threshold=10, reset_timeout_s=30.0)

    async def always_fail():
        raise LLMBackendError("nope")

    async def driver():
        t0 = time.monotonic()
        await with_fallback(
            chain=[("a", always_fail), ("b", always_fail)],
            label="backoff",
            circuit_breaker_registry=registry,
            sleep_between=0.01,
        )
        return time.monotonic() - t0

    elapsed = asyncio.run(driver())
    # 2 项 chain → 失败切换 1 次（attempt 0 → 0.01s）
    # Windows 全套测试慢时 elapsed 可能溢出，但 base=0.01 应该够短
    assert elapsed >= 0.01, f"expected >= 0.01s, got {elapsed}"
    assert elapsed < 0.50, f"expected < 0.50s (not too long), got {elapsed}"


def test_unavailable_error_updates_breaker():
    """LLMUnavailableError（5xx / 超时）→ breaker.on_failure() 计 1 次失败。"""
    registry = CircuitBreakerRegistry(failure_threshold=2, reset_timeout_s=30.0)

    async def unavailable():
        raise LLMUnavailableError("5xx")

    async def driver():
        await with_fallback(
            chain=[("a", unavailable)],
            label="unavailable_test",
            circuit_breaker_registry=registry,
        )

    asyncio.run(driver())
    cb = registry.get_or_create("a")
    # 1 次失败，未到 threshold，仍是 closed
    assert cb._stats.failure_count == 1


def test_no_breaker_registry_works_as_before():
    """circuit_breaker_registry=None 时，with_fallback 行为与 V1.5 完全一致。"""

    async def always_fail():
        raise LLMBackendError("nope")

    result = asyncio.run(
        with_fallback(
            chain=[("a", always_fail)],
            label="no_breaker",
            circuit_breaker_registry=None,
        )
    )
    assert result.final_status == "all_failed"
    assert result.trail == [("a", "error: nope")]
