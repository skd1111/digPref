"""熔断器测试。"""
import time

from agent.llm.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitState,
)


def test_initial_state_closed():
    cb = CircuitBreaker("test", failure_threshold=3)
    assert cb.state == CircuitState.CLOSED
    assert cb.allow() is True


def test_three_failures_open():
    cb = CircuitBreaker("test", failure_threshold=3, reset_timeout_s=0.05)
    cb.on_failure()
    cb.on_failure()
    assert cb.state == CircuitState.CLOSED
    cb.on_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow() is False


def test_half_open_probe_success():
    cb = CircuitBreaker("test", failure_threshold=3, reset_timeout_s=0.5)
    for _ in range(3):
        cb.on_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.6)
    # 触发 half_open 转移：调用 allow() 一次（_maybe_to_half_open 在内部跑）
    _ = cb.allow()
    assert cb.state == CircuitState.HALF_OPEN
    cb.on_success()  # 探测成功
    assert cb.state == CircuitState.CLOSED


def test_half_open_probe_failure():
    cb = CircuitBreaker("test", failure_threshold=3, reset_timeout_s=0.5)
    for _ in range(3):
        cb.on_failure()
    time.sleep(0.6)
    _ = cb.allow()  # 触发 half_open
    assert cb.state == CircuitState.HALF_OPEN
    cb.on_failure()  # 探测失败
    assert cb.state == CircuitState.OPEN


def test_registry_get_or_create():
    reg = CircuitBreakerRegistry(failure_threshold=2)
    a = reg.get_or_create("backend_a")
    b = reg.get_or_create("backend_a")
    assert a is b  # 同一实例
    assert "backend_a" in reg.all_states()


def test_registry_reset_all():
    reg = CircuitBreakerRegistry(failure_threshold=2)
    cb = reg.get_or_create("a")
    for _ in range(2):
        cb.on_failure()
    assert cb.state == CircuitState.OPEN
    reg.reset_all()
    assert cb.state == CircuitState.CLOSED
