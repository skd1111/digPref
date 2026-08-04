"""test_phase12_v1 —— Phase 12 V1 多智能体调度扩展测试。

覆盖：
- worker_pool：并发限流 + 重试 + DLQ + 幂等去重 + 取消传播
- locks：乐观 CAS + 字典序分布式锁
- token_bucket：三层限流 + 降级链 + 红线保护（is_local_only_task）
- hitl_bridge：请求审批 + auto_approve_low_risk + 决策 reject / approve

CLAUDE.md §1/§2/§6 红线：
- HITL 不可绕过（hitl_bridge 必审批）
- 派生树硬上限（V0 已测；V1 不改）
- 敏感任务本机（is_local_only_task 强制 ollama）
"""
from __future__ import annotations

import asyncio

import pytest

from agent.orchestrator.worker_pool import (
    DLQEntry,
    WorkerPool,
    WorkerResult,
    WorkerTask,
)
from agent.orchestrator.locks import (
    DistributedLockManager,
    VersionedState,
    cas_update,
)
from agent.orchestrator.token_bucket import (
    TokenBucket,
    TokenBucketManager,
)
from agent.orchestrator.hitl_bridge import (
    HITLBridge,
    HITLDecision,
    HITLRequest,
    reset_default_hitl_bridge,
)


# ---- Worker Pool ----------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_pool_success_first_try():
    pool = WorkerPool(concurrency=2, retry_base_delay_s=0.01)

    async def handler(payload):
        return {"echo": payload}

    result = await pool.submit(
        idempotency_token="t1", payload={"x": 1}, handler=handler,
    )
    assert result.success is True
    assert result.attempts == 1
    assert result.result == {"echo": {"x": 1}}
    assert pool.dlq_entries == []
    assert pool.task_count == 1


@pytest.mark.asyncio
async def test_worker_pool_retries_then_dlq():
    """失败 3 次 → DLQ。"""
    pool = WorkerPool(concurrency=1, retry_base_delay_s=0.01, max_attempts=3)

    async def always_fail(payload):
        raise ValueError("nope")

    result = await pool.submit(
        idempotency_token="t1", payload={"x": 1}, handler=always_fail,
    )
    assert result.success is False
    assert result.attempts == 3
    assert "nope" in result.error
    assert len(pool.dlq_entries) == 1
    assert pool.dlq_entries[0].task_id == result.task_id


@pytest.mark.asyncio
async def test_worker_pool_retry_succeeds_on_second_try():
    """第 2 次成功 → 不进 DLQ。"""
    pool = WorkerPool(concurrency=1, retry_base_delay_s=0.01, max_attempts=3)
    counter = {"n": 0}

    async def flaky(payload):
        counter["n"] += 1
        if counter["n"] < 2:
            raise ValueError("transient")
        return "ok"

    result = await pool.submit(
        idempotency_token="t1", payload={}, handler=flaky,
    )
    assert result.success is True
    assert result.attempts == 2
    assert pool.dlq_entries == []


@pytest.mark.asyncio
async def test_worker_pool_idempotency_dedup():
    """同 idempotency_token 提交两次 → 只跑一次。"""
    pool = WorkerPool(concurrency=1, retry_base_delay_s=0.01)
    calls = []

    async def handler(payload):
        calls.append(payload)
        return payload

    r1 = await pool.submit(
        idempotency_token="dup", payload={"x": 1}, handler=handler,
    )
    r2 = await pool.submit(
        idempotency_token="dup", payload={"x": 2}, handler=handler,
    )
    assert len(calls) == 1  # 第二次 dedup 命中
    assert r1.task_id == r2.task_id


@pytest.mark.asyncio
async def test_worker_pool_concurrency_limit():
    """并发限流：N 个任务 + 并发 2 → 同时最多 2 个 handler 跑。"""
    pool = WorkerPool(concurrency=2, retry_base_delay_s=0.01)
    inflight = 0
    max_inflight = 0

    async def handler(payload):
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0.05)
        inflight -= 1
        return payload["id"]

    tasks = [
        pool.submit(
            idempotency_token=f"t{i}", payload={"id": i}, handler=handler,
        )
        for i in range(5)
    ]
    results = await asyncio.gather(*tasks)
    assert all(r.success for r in results)
    assert max_inflight <= 2  # 受 semaphore 约束


@pytest.mark.asyncio
async def test_worker_pool_cancel_all():
    """cancel_all → 所有 worker 软停止。"""
    pool = WorkerPool(concurrency=2, retry_base_delay_s=0.01)

    async def slow(payload):
        await asyncio.sleep(5.0)
        return "done"

    # 启一个长任务
    task = asyncio.create_task(pool.submit(
        idempotency_token="t1", payload={}, handler=slow,
    ))
    await asyncio.sleep(0.02)  # 让 worker 开始
    pool.cancel_all()
    # 后续 submit 应立即返 cancelled
    result = await pool.submit(
        idempotency_token="t2", payload={}, handler=slow,
    )
    assert result.success is False
    assert result.error == "cancelled"
    task.cancel()


# ---- Locks ---------------------------------------------------------------


def test_cas_update_basic():
    state = VersionedState(data={"count": 0}, state_version=0)

    def increment(d):
        d["count"] += 1
        return d

    success, ver = cas_update(state, increment)
    assert success is True
    assert ver == 1
    assert state.data["count"] == 1


def test_cas_update_none_returns_false():
    state = VersionedState(data={"x": 1}, state_version=0)

    def reject(_):
        return None

    success, ver = cas_update(state, reject)
    assert success is False
    assert ver == 0
    assert state.data == {"x": 1}


@pytest.mark.asyncio
async def test_distributed_lock_basic():
    mgr = DistributedLockManager(default_ttl_s=10.0)
    async with mgr.acquire_one("res_a") as held:
        assert held is True
    # 释放后可再获取
    async with mgr.acquire_one("res_a") as held:
        assert held is True


@pytest.mark.asyncio
async def test_distributed_lock_acquire_many_lexicographic():
    """acquire_many 按字典序获取 —— 防止 ABBA 死锁。"""
    mgr = DistributedLockManager(default_ttl_s=10.0)
    held_order: list[str] = []
    async with mgr.acquire_many(["res_b", "res_a", "res_c"]) as held:
        # 验证是按字典序获取的（虽然 V1 简化是顺序 await，但顺序应该升序）
        held_order.extend(held)
    assert held_order == ["res_a", "res_b", "res_c"]


# ---- Token Bucket ---------------------------------------------------------


def test_token_bucket_consume_initial_full():
    bucket = TokenBucket(capacity=10, refill_rate=1.0, tokens=10.0)
    assert bucket.try_consume(5) is True
    assert bucket.try_consume(5) is True
    assert bucket.try_consume(1) is False  # 用完了


def test_token_bucket_refill():
    """时间流逝补充令牌。"""
    import time
    bucket = TokenBucket(capacity=10, refill_rate=10.0, tokens=0.0)
    assert bucket.try_consume(1) is False
    time.sleep(0.5)  # 等 0.5s 应补充 5 个
    assert bucket.try_consume(1) is True


def test_token_bucket_manager_three_dimensions():
    mgr = TokenBucketManager(
        default_capacity=5, default_refill_rate=0.001,  # 几乎不 refill
        backend_overrides={"private": (5, 0.001), "ollama": (5, 0.001)},
    )
    # 三维 key 互不影响
    assert mgr.try_consume("tenant_a", "plan", "private", n=5) is True
    assert mgr.try_consume("tenant_a", "plan", "private", n=1) is False
    # 不同 task_type 独立
    assert mgr.try_consume("tenant_a", "summarise", "private", n=5) is True
    # 不同 backend 独立
    assert mgr.try_consume("tenant_a", "plan", "ollama", n=5) is True


def test_token_bucket_manager_fallback_chain():
    mgr = TokenBucketManager()
    # private 几乎无限；直接返 private
    assert mgr.fallback_backend("t", "plan", "private") == "private"
    # private 用尽 → ollama
    while mgr.try_consume("t", "plan", "private", n=1):
        pass
    assert mgr.fallback_backend("t", "plan", "private") in ("ollama", "local_small", "mock")


def test_token_bucket_local_only_redline():
    """_LOCAL_ONLY_TASKS 即使私有桶满也强制走 ollama（不降级到 mock）。"""
    mgr = TokenBucketManager()
    # 把 private 桶耗尽
    while mgr.try_consume("t", "intent", "private", n=1):
        pass
    # 但 is_local_only_task=True → 强制走 ollama（不 mock）
    backend = mgr.fallback_backend("t", "intent", "private", is_local_only_task=True)
    assert backend in ("ollama", "local_small")  # 不到 mock


def test_token_bucket_manager_reset():
    mgr = TokenBucketManager(
        default_capacity=2, default_refill_rate=0.001,
        backend_overrides={"private": (2, 0.001)},
    )
    assert mgr.try_consume("t", "plan", "private", n=2) is True
    assert mgr.try_consume("t", "plan", "private", n=1) is False
    mgr.reset()
    assert mgr.try_consume("t", "plan", "private", n=2) is True


# ---- HITL Bridge ----------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_bridge_low_risk_auto_approve():
    bridge = HITLBridge()
    decision = await bridge.request_approval(
        sub_agent_id="sub-1",
        parent_run_id="run-1",
        operation="INSERT INTO log VALUES (...)",
        target="log_db",
        risk_level="low",
    )
    assert isinstance(decision, HITLDecision)
    assert decision.decision == "approve"


@pytest.mark.asyncio
async def test_hitl_bridge_high_risk_default_reject():
    """V1 简化：高 risk 默认 reject（前端审批 UI 未接；防止意外副作用）。"""
    bridge = HITLBridge()
    decision = await bridge.request_approval(
        sub_agent_id="sub-1",
        parent_run_id="run-1",
        operation="UPDATE orders SET status='paid'",
        target="orders_db.orders",
        risk_level="high",
    )
    assert decision.decision == "reject"


@pytest.mark.asyncio
async def test_hitl_bridge_request_has_correlation_id():
    """correlation_id 默认 = parent_run_id:sub_agent_id。"""
    import agent.orchestrator.hitl_bridge as hitl_mod

    captured: dict = {}

    async def fake_audit(action, payload):
        captured[action] = payload

    original = hitl_mod.audit
    hitl_mod.audit = fake_audit
    try:
        bridge = HITLBridge()
        decision = await bridge.request_approval(
            sub_agent_id="sub-1",
            parent_run_id="run-1",
            operation="DELETE FROM x",
            target="x_table",
            risk_level="medium",
        )
        # 应该有 SUB_AGENT_HITL_REQUESTED + SUB_AGENT_HITL_DECIDED
        assert "SUB_AGENT_HITL_REQUESTED" in captured
        assert "SUB_AGENT_HITL_DECIDED" in captured
        # request 的 correlation_id 应已填
        assert captured["SUB_AGENT_HITL_REQUESTED"]["correlation_id"]
    finally:
        hitl_mod.audit = original


# ---- 集成：Token Bucket + Worker Pool -------------------------------------


@pytest.mark.asyncio
async def test_integration_bucket_then_pool():
    """bucket 控制 pool 接受的任务量（V1 简化：手动配合）。"""
    mgr = TokenBucketManager()
    pool = WorkerPool(concurrency=1, retry_base_delay_s=0.01)

    async def handler(p):
        return "ok"

    # 模拟：5 次请求，每次先 token bucket 检查
    succeeded = 0
    for i in range(5):
        if mgr.try_consume("t", "plan", "private"):
            result = await pool.submit(
                idempotency_token=f"t{i}", payload={}, handler=handler,
            )
            if result.success:
                succeeded += 1
    # private 默认容量 50 + 5/s refill → 5 次都成功
    assert succeeded == 5