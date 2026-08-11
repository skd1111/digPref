"""Phase 2C V2 — SSE 三处同步修复（CLAUDE.md §4）。

覆盖：
  - RouterEngine.route_request() 末尾 emit_event("llm_route_decided")
  - with_fallback 全链失败时 emit_event("llm_degraded")
  - graph/stream.py _CHANNEL_BY_KIND 含 3 个 llm_* 通道
  - stream.py 流循环 _drain_router_events()
"""

import pytest
from agent.llm.budget import BudgetController
from agent.llm.circuit_breaker import CircuitBreakerRegistry
from agent.llm.engine import RouterEngine
from agent.llm.metrics import (
    MetricsRecorder,
    consume_router_events,
    flush_router_events,
)
from agent.llm.models import LLMBackend, Sensitivity, TaskCategory


@pytest.fixture(autouse=True)
def _clean_queue():
    """每个测试前清空事件队列（避免测试间串扰）。"""
    flush_router_events()
    yield
    flush_router_events()


def test_engine_route_request_emits_llm_route_decided():
    """RouterEngine.route_request() 末尾 emit 1 个 llm_route_decided 事件。"""
    backends = [
        LLMBackend(
            name="local-1",
            type="local",
            base_url="http://localhost",
            model_name="test",
            data_residency="local",
            enabled=True,
            role="execution",
        ),
    ]
    metrics = MetricsRecorder()
    eng = RouterEngine(
        backends=backends,
        budget=BudgetController(),
        breakers=CircuitBreakerRegistry(),
        metrics=metrics,
    )

    eng.route_request(
        task_kind="intent",
        category=TaskCategory.SIMPLE,
        sensitivity=Sensitivity.PUBLIC,
        request_id="test-req-1",
    )
    # 走 route_request 后 emit 1 个事件
    import asyncio

    events = asyncio.run(consume_router_events(timeout_s=0.01))
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "llm_route_decided"
    assert payload["request_id"] == "test-req-1"
    assert payload["primary_backend"] == "local-1"
    assert payload["actual_backend"] == "local-1"


def test_fallback_emits_llm_degraded_when_all_fail():
    """with_fallback 全链失败 → emit 1 个 llm_degraded 事件。"""
    from agent.llm.fallback import LLMBackendError, with_fallback

    async def always_fail():
        raise LLMBackendError("simulated")

    import asyncio

    asyncio.run(
        with_fallback(
            chain=[("a", always_fail), ("b", always_fail), ("c", always_fail)],
            label="test_label",
            raise_on_all_fail=False,
        )
    )
    events = asyncio.run(consume_router_events(timeout_s=0.01))
    # 可能有 engine_no_candidates_after_rules 之类干扰，只关心 llm_degraded
    degraded = [e for e in events if e[0] == "llm_degraded"]
    assert len(degraded) == 1
    _kind, payload = degraded[0]
    assert payload["label"] == "test_label"
    assert payload["chain_len"] == 3
    assert payload["fallback_used_count"] == 3


def test_graph_stream_channel_mapping_includes_three_llm_events():
    """graph/stream.py _CHANNEL_BY_KIND 含 3 个 llm_* 通道（CLAUDE.md §4 三处同步）。"""
    from agent.graph import stream as stream_mod

    expected = {
        "llm_route_decided": "agent://llm_route_decided",
        "llm_degraded": "agent://llm_degraded",
        "llm_budget_alert": "agent://llm_budget_alert",
    }
    for kind, channel in expected.items():
        assert stream_mod._CHANNEL_BY_KIND.get(kind) == channel, (
            f"missing SSE channel mapping for {kind}"
        )


def test_engine_no_candidates_still_emits():
    """硬规则过滤后 0 candidates（极端情况）→ 仍 emit 1 个事件（让前端知道决策失败）。"""
    backends = [
        # 全部 cloud，但 sensitivity=PRODUCTION 应被硬规则踢出
        LLMBackend(
            name="cloud-1",
            type="cloud",
            base_url="https://api.openai.com/v1",
            model_name="gpt-4o",
            data_residency="cloud",
            enabled=True,
            role="execution",
        ),
    ]
    eng = RouterEngine(
        backends=backends,
        budget=BudgetController(),
        breakers=CircuitBreakerRegistry(),
        metrics=MetricsRecorder(),
    )
    decision = eng.route_request(
        task_kind="plan",
        category=TaskCategory.COMPLEX,
        sensitivity=Sensitivity.PRODUCTION,  # 强制私有
        request_id="no-candidates",
    )
    assert decision.actual_backend is None
    import asyncio

    events = asyncio.run(consume_router_events(timeout_s=0.01))
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "llm_route_decided"
    assert payload["actual_backend"] is None
