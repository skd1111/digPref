"""Phase 2C V2 — Spark 模式（双跳 reasoning → execution）。

覆盖：
  - LMRouter.set_spark_mode() 切到 True 后 4 公开方法走 spark_route
  - spark_route 调两次 route_request（一次 role_override='reasoning'，一次 'execution'）
  - 决策融合：execution.actual_backend + spark_reasoning_backend / spark_execution_backend 字段
  - V2.0 placeholder 返回（无真实 LLM 调用）
"""
import pytest

from agent.llm.engine import RouterEngine
from agent.llm.budget import BudgetController
from agent.llm.circuit_breaker import CircuitBreakerRegistry
from agent.llm.metrics import MetricsRecorder
from agent.llm.models import LLMBackend
from agent.llm.router import LMRouter


@pytest.fixture
def multi_role_engine():
    """构造含 reasoning + execution + utility 三类后端的 Engine。"""
    backends = [
        LLMBackend(name="ollama-utility", type="local", base_url="http://x",
                   model_name="qwen2.5:0.5b", data_residency="local",
                   role="utility", enabled=True),
        LLMBackend(name="deepseek-reasoning", type="private", base_url="http://y",
                   model_name="r1", data_residency="private",
                   role="reasoning", enabled=True),
        LLMBackend(name="gpt4o-execution", type="cloud", base_url="http://z",
                   model_name="gpt-4o", data_residency="cloud",
                   role="execution", enabled=True, api_key_ref="k"),
    ]
    return RouterEngine(
        backends=backends,
        budget=BudgetController(),
        breakers=CircuitBreakerRegistry(),
        metrics=MetricsRecorder(),
        spark_enabled=False,
    )


@pytest.fixture(autouse=True)
def _clean_router_queue():
    from agent.llm.metrics import flush_router_events
    flush_router_events()
    yield
    flush_router_events()


def test_spark_route_uses_role_override_twice(multi_role_engine):
    """spark_route 调 2 次 route_request（role='reasoning' + 'execution'）。"""
    import asyncio

    decision = asyncio.run(multi_role_engine.spark_route(
        task_kind="plan",
        user_prompt="分析这个 SQL 性能问题",
        history=[],
        tool_specs=[],
        request_id="spark-test",
    ))
    # execution 是 final decision
    assert decision.actual_backend == "gpt4o-execution"
    # reasoning 是 spark_reasoning_backend
    assert decision.spark_reasoning_backend == "deepseek-reasoning"
    assert decision.spark_execution_backend == "gpt4o-execution"
    # V2.0 placeholder 字段填充
    assert "[reasoning draft" in (decision.spark_draft or "")
    assert "[execution output" in (decision.spark_execution_output or "")


def test_lm_router_set_spark_mode(multi_role_engine):
    """LMRouter.set_spark_mode(True) 后 plan() 走 spark_route。"""
    import asyncio

    router = LMRouter(engine=multi_role_engine)
    assert router.spark_mode is False

    router.set_spark_mode(True)
    assert router.spark_mode is True
    # Engine 端也同步
    assert multi_role_engine.spark_enabled is True

    plan_steps, explanation = asyncio.run(router.plan(
        intent="query",
        user_prompt="test spark mode",
        history=[],
        tool_specs=[],
    ))
    # V2.0 placeholder：空 plan + 含 "Spark V0 placeholder" 标识
    assert plan_steps == []
    assert "Spark V0 placeholder" in explanation
    assert "deepseek-reasoning" in explanation


def test_lm_router_set_spark_mode_off_keeps_legacy(multi_role_engine, monkeypatch):
    """LMRouter.set_spark_mode(False) 后 plan() 走 V1.5 兼容路径（mock_dispatch）。"""
    import asyncio

    monkeypatch.setenv("EAIDE_LLM_BACKEND", "mock")
    router = LMRouter(engine=multi_role_engine)
    # 不开 spark
    plan_steps, explanation = asyncio.run(router.plan(
        intent="query",
        user_prompt="test legacy",
        history=[],
        tool_specs=[],
    ))
    # mock 返回固定 plan，**不含** "Spark V0 placeholder"
    assert isinstance(plan_steps, list)
    assert "Spark V0 placeholder" not in explanation


def test_spark_mode_emits_two_decisions(multi_role_engine):
    """spark_route 触发 2 次 emit_event('llm_route_decided')（reasoning + execution）。"""
    import asyncio
    from agent.llm.metrics import consume_router_events

    asyncio.run(multi_role_engine.spark_route(
        task_kind="summarise",
        user_prompt="summarize this",
        history=[],
        tool_specs=[],
        request_id="spark-emit",
    ))
    events = asyncio.run(consume_router_events(timeout_s=0.01))
    # spark_route 内调 2 次 route_request → 2 次 emit
    # 第 2 次 emit 附加 spark_mode=True 标识
    assert len(events) >= 2
    spark_marked = [e for e in events if e[1].get("spark_mode") is True]
    assert len(spark_marked) >= 1
    payload = spark_marked[-1][1]
    assert payload["spark_reasoning_backend"] == "deepseek-reasoning"
    assert payload["spark_execution_backend"] == "gpt4o-execution"


def test_spark_role_override_filters_to_specific_role(multi_role_engine):
    """spark_route(role='reasoning') 时 _RESIDENCY_ALLOW + role_override 双重过滤。

    比如 execution 角色是 cloud，但 sensitivity=PRODUCTION 应被硬规则踢出；
    spark 第二跳因 role_override='execution' 找不到 local/private backend → 0 candidates。
    """
    from agent.llm.models import Sensitivity, TaskCategory

    decision = multi_role_engine.route_request(
        task_kind="plan",
        category=TaskCategory.COMPLEX,
        sensitivity=Sensitivity.PRODUCTION,  # 强制私有
        role_override="execution",  # 但 Spark 第二跳要 execution（cloud）
    )
    assert decision.actual_backend is None, \
        "PRODUCTION sensitivity 强制私有，不应选 cloud execution backend"