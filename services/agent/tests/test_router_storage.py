"""router.db 存储层测试（Phase 2C v2）。

依赖 conftest.py 的 autouse _isolate fixture：chdir 到 tmp_path，
settings.llm_router_db_path 默认相对路径 "router.db" → 自动落在临时目录，测试隔离。
"""

from __future__ import annotations

import pytest
from agent.llm import storage
from agent.llm.models import (
    LLMBackend,
    RoutingDecision,
    Sensitivity,
    TaskCategory,
)


def _backend(
    name="deepseek",
    residency="private",
    cost=0.5,
    type="private",
    enabled=True,
) -> LLMBackend:
    return LLMBackend(
        name=name,
        type=type,
        base_url="http://172.1.0.134:8000/v1",
        model_name="DeepSeek-RD-Llama-70B-Int8",
        api_key_ref=f"llm.{name}.api_key",
        capabilities=["code", "plan"],
        cost_per_1k_tokens=cost,
        data_residency=residency,
        enabled=enabled,
    )


async def test_upsert_and_get_backend():
    await storage.upsert_backend(_backend())
    got = await storage.get_backend("deepseek")
    assert got is not None
    assert got.name == "deepseek"
    assert got.model_name == "DeepSeek-RD-Llama-70B-Int8"
    assert got.capabilities == ["code", "plan"]
    assert got.data_residency == "private"
    # api_key_ref 只是占位符引用，不是明文
    assert got.api_key_ref == "llm.deepseek.api_key"


async def test_upsert_is_idempotent_update():
    await storage.upsert_backend(_backend(cost=0.5))
    await storage.upsert_backend(_backend(cost=0.9))  # 同名 → 更新
    all_backends = await storage.list_backends()
    assert len(all_backends) == 1
    assert all_backends[0].cost_per_1k_tokens == 0.9


async def test_list_backends_enabled_only():
    await storage.upsert_backend(_backend(name="on"))
    disabled = _backend(name="off")
    disabled.enabled = False
    await storage.upsert_backend(disabled)

    all_b = await storage.list_backends()
    enabled = await storage.list_backends(enabled_only=True)
    assert {b.name for b in all_b} == {"on", "off"}
    assert {b.name for b in enabled} == {"on"}


async def test_delete_backend():
    await storage.upsert_backend(_backend())
    assert await storage.delete_backend("deepseek") is True
    assert await storage.get_backend("deepseek") is None
    # 删不存在的返回 False
    assert await storage.delete_backend("ghost") is False


async def test_upsert_enabled_disables_same_residency_others():
    """同驻留只允许 1 个启用：启用 private-b 时，类型为 cloud 但驻留 private 的 cloud-a 自动停用。"""
    await storage.upsert_backend(
        _backend(name="cloud-a", type="cloud", residency="private", enabled=True)
    )
    await storage.upsert_backend(
        _backend(name="private-b", type="private", residency="private", enabled=True)
    )
    all_b = {b.name: b.enabled for b in await storage.list_backends()}
    assert all_b["cloud-a"] is False
    assert all_b["private-b"] is True


async def test_upsert_enabled_does_not_disable_other_residencies():
    """不同驻留互不影响：启用驻留 private 的模型不触碰已启用的 cloud 驻留模型。"""
    await storage.upsert_backend(
        _backend(name="cloud-a", type="cloud", residency="cloud", enabled=True)
    )
    await storage.upsert_backend(
        _backend(name="private-a", type="private", residency="private", enabled=True)
    )
    all_b = {b.name: b.enabled for b in await storage.list_backends()}
    assert all_b["cloud-a"] is True
    assert all_b["private-a"] is True


async def test_upsert_disabled_does_not_trigger_exclusion():
    """停用/保存 disabled 不触发互斥（不自动停用同驻留其它）。"""
    await storage.upsert_backend(
        _backend(name="cloud-a", type="cloud", residency="cloud", enabled=True)
    )
    await storage.upsert_backend(
        _backend(name="cloud-b", type="cloud", residency="cloud", enabled=False)
    )
    all_b = {b.name: b.enabled for b in await storage.list_backends()}
    assert all_b["cloud-a"] is True
    assert all_b["cloud-b"] is False


async def test_record_decision_and_recent():
    decision = RoutingDecision(
        request_id="req-1",
        user_id="alice",
        task_category=TaskCategory.SIMPLE,
        sensitivity=Sensitivity.PII,
        primary_backend="local_small",
        actual_backend="local_small",
        fallback_chain=["local_small", "ollama"],
    )
    await storage.record_decision(decision, est_tokens=100, now=1_700_000_000)
    recent = await storage.recent_decisions(limit=10)
    assert len(recent) == 1
    assert recent[0]["request_id"] == "req-1"
    assert recent[0]["actual_backend"] == "local_small"
    assert recent[0]["trace"]["sensitivity"] == "pii"


async def test_cost_daily_aggregation():
    """两次同 (date,user,backend,category) 调用 → 聚合成一行，call_count=2。"""
    d1 = RoutingDecision(
        request_id="r1",
        user_id="bob",
        task_category=TaskCategory.COMPLEX,
        primary_backend="cloud",
        actual_backend="cloud",
        actual_cost=0.03,
    )
    d2 = RoutingDecision(
        request_id="r2",
        user_id="bob",
        task_category=TaskCategory.COMPLEX,
        primary_backend="cloud",
        actual_backend="cloud",
        actual_cost=0.05,
    )
    ts = 1_700_000_000  # 同一天
    await storage.record_decision(d1, est_tokens=200, now=ts)
    await storage.record_decision(d2, est_tokens=300, now=ts)

    summary = await storage.cost_summary()
    assert len(summary) == 1
    row = summary[0]
    assert row["call_count"] == 2
    assert row["total_tokens"] == 500
    assert row["total_cost"] == pytest.approx(0.08)


async def test_cache_hit_not_counted_in_cost():
    """cache_hit=True → actual_cost 记 0，tokens 记 0。"""
    d = RoutingDecision(
        request_id="cached",
        user_id="carol",
        task_category=TaskCategory.SIMPLE,
        primary_backend="local_small",
        actual_backend="local_small",
        cache_hit=True,
        actual_cost=0.0,
    )
    await storage.record_decision(d, est_tokens=999, now=1_700_000_000)
    summary = await storage.cost_summary()
    assert summary[0]["total_tokens"] == 0
    assert summary[0]["total_cost"] == pytest.approx(0.0)
