"""test_phase12_v15 —— Phase 12 V1.5 完整集成测试。

覆盖：
- events：进程内 deque 路由（4 通道：spawn / progress / done / approval）
- sensitive：5 类 PII / DB 凭证 / SQL 错误 / DDL/DML 启发式
- context_strategy：3 类策略自动选型 + 必读字段不可压 + 压缩率 + shared pool
- state_repo：4 表（tasks / artifacts / dlq / metrics）CRUD + CAS 冲突 + 幂等去重
- queue：3 级优先级 + 幂等 + 关闭唤醒
- audit_bridge：11 类事件 + replay_tree + build_tree + replay_summary
- eval_collector：12 项指标 + 阈值告警 + 确定性抽样 + Judge 解析
- hitl_bridge V1.5：`wait_for_user=True` 复用主图 interrupt + 超时 reject
- observability：scrub PII + 写 JSONL（不被破坏）
- orchestrator V1.5：dispatch → enqueue → run_until_drained → 落库 → SSE emit
- api：路由顺序（list / dlq / metrics / queue / tree 在通配符前）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---- events ---------------------------------------------------------------


def test_events_emit_and_consume():
    from agent.orchestrator import events as evt
    evt.flush_orchestrator_events()
    evt.emit_orchestrator_event(evt.EVT_SUB_AGENT_SPAWN, {"sub_agent_id": "s1"})
    evt.emit_orchestrator_event(evt.EVT_SUB_AGENT_PROGRESS, {"sub_agent_id": "s1", "attempt": 1})
    evt.emit_orchestrator_event(evt.EVT_SUB_AGENT_DONE, {"sub_agent_id": "s1", "status": "ok"})

    async def run():
        out = await evt.consume_orchestrator_events()
        return out
    items = asyncio.run(run())
    assert len(items) == 3
    kinds = [k for k, _ in items]
    assert kinds == [evt.EVT_SUB_AGENT_SPAWN, evt.EVT_SUB_AGENT_PROGRESS, evt.EVT_SUB_AGENT_DONE]
    evt.flush_orchestrator_events()


def test_events_reject_unknown_kind():
    from agent.orchestrator.events import emit_orchestrator_event
    with pytest.raises(ValueError, match="未知"):
        emit_orchestrator_event("totally_bogus", {})


# ---- sensitive ------------------------------------------------------------


def test_sensitive_pii_in_text():
    from agent.orchestrator.sensitive import prompt_safe_for_remote
    safe, hits = prompt_safe_for_remote("用户电话 13800001111 已加白名单")
    assert not safe
    assert any(h.startswith("pii:phone") for h in hits)


def test_sensitive_db_credential_in_payload():
    from agent.orchestrator.sensitive import prompt_safe_for_remote
    safe, _ = prompt_safe_for_remote("", {"password": "hunter2", "user": "root"})
    assert not safe


def test_sensitive_oracle_error():
    from agent.orchestrator.sensitive import prompt_safe_for_remote
    safe, hits = prompt_safe_for_remote("ORA-00904: invalid column name")
    assert not safe
    assert any("sql_error:oracle_error" == h for h in hits)


def test_sensitive_select_from_resultset():
    from agent.orchestrator.sensitive import prompt_safe_for_remote
    safe, _ = prompt_safe_for_remote("SELECT * FROM orders WHERE id = ?")
    assert not safe


def test_sensitive_clean_text():
    from agent.orchestrator.sensitive import prompt_safe_for_remote
    safe, hits = prompt_safe_for_remote("请帮我总结本月 OKR")
    assert safe
    assert hits == []


def test_sensitive_classify_spec_local_only_task():
    from agent.orchestrator.sensitive import classify_spec
    from agent.orchestrator.spec import SubAgentSpec
    spec = SubAgentSpec(
        sub_agent_id="s", parent_run_id="r", depth=1,
        task_type="intent", task_description="识别意图",
    )
    v = classify_spec(spec, "")
    assert v.local_only is True
    assert any("local_only_task:intent" == r for r in v.reasons)


# ---- context_strategy -----------------------------------------------------


def test_context_strategy_passthrough_simple():
    from agent.orchestrator.context_strategy import build_context
    from agent.orchestrator.spec import SubAgentSpec, ContextPolicy
    spec = SubAgentSpec(
        sub_agent_id="s", parent_run_id="r", depth=1,
        task_type="plan", task_description="改个 bug",
        input_payload={"file": "a.py", "line": 42},
        context_policy=ContextPolicy(strategy="passthrough"),
    )
    ctx = build_context(spec)
    assert ctx.strategy == "passthrough"
    assert "file: a.py" in ctx.prompt
    assert "line: 42" in ctx.prompt
    assert ctx.compression_ratio == 0.0


def test_context_strategy_required_fields_kept():
    from agent.orchestrator.context_strategy import build_context
    from agent.orchestrator.spec import SubAgentSpec, ContextPolicy
    spec = SubAgentSpec(
        sub_agent_id="s", parent_run_id="r", depth=1,
        task_type="plan", task_description="改 bug",
        input_payload={
            "error_code": "ORA-00904",
            "status_code": 500,
            "trace": "x" * 5000,  # 必读但超长（仍保留原文）
        },
        context_policy=ContextPolicy(
            strategy="incremental_summary",
            required_fields=["error_code", "status_code"],
            max_summary_tokens=200,
        ),
    )
    ctx = build_context(spec)
    assert ctx.required_fields_kept is True
    assert "error_code: ORA-00904" in ctx.prompt
    assert "status_code: 500" in ctx.prompt


def test_context_strategy_auto_pick_incremental_for_long():
    from agent.orchestrator.context_strategy import select_strategy
    from agent.orchestrator.spec import SubAgentSpec
    spec = SubAgentSpec(
        sub_agent_id="s", parent_run_id="r", depth=1,
        task_type="plan", task_description="x",
        input_payload={"x": "y" * 20000},
    )
    assert select_strategy(spec) == "incremental_summary"


def test_context_strategy_shared_memory_pool_roundtrip():
    from agent.orchestrator.context_strategy import (
        SharedMemoryPool, build_context,
    )
    from agent.orchestrator.spec import SubAgentSpec, ContextPolicy
    pool = SharedMemoryPool()
    pool.set_fact("run-1", "sql_schema", {"tables": ["orders", "users"]})
    spec = SubAgentSpec(
        sub_agent_id="s", parent_run_id="run-1", depth=1,
        task_type="plan", task_description="使用共享池",
        input_payload={"hint": "no data"},
        context_policy=ContextPolicy(
            strategy="shared_memory_pool",
            shared_keys=["sql_schema"],
        ),
    )
    ctx = build_context(spec, pool=pool)
    assert "sql_schema" in ctx.prompt
    assert "tables" in ctx.prompt
    assert ctx.shared_facts_used == ["sql_schema"]


def test_context_strategy_estimate_tokens_cjk_and_ascii():
    from agent.orchestrator.context_strategy import estimate_tokens
    assert estimate_tokens("") == 0
    # 全 ASCII：约 4 字符 / token
    assert 1 <= estimate_tokens("hi") <= 5
    # CJK：约 1 字符 / token
    assert estimate_tokens("中文测试") >= 3


def test_context_strategy_overflow_value_externalized():
    from agent.orchestrator.context_strategy import build_context
    from agent.orchestrator.spec import SubAgentSpec, ContextPolicy
    spec = SubAgentSpec(
        sub_agent_id="s", parent_run_id="r", depth=1,
        task_type="plan", task_description="x",
        input_payload={"huge": "Y" * 5000},  # 远超 400 inline 限额
    )
    ctx = build_context(spec)
    assert len(ctx.raw_refs) >= 1
    assert ctx.raw_refs[0].content_hash


# ---- state_repo -----------------------------------------------------------


@pytest.mark.asyncio
async def test_state_repo_cas_conflict():
    from agent.orchestrator.state_repo import (
        StateRepo, StateVersionConflict, reset_default_repo,
    )
    repo = reset_default_repo()
    await repo.save_task(
        task_id="t1", parent_run_id="r1", parent_task_id=None,
        correlation_id="c1", idempotency_token="idem-1", depth=1,
        task_type="plan", priority="normal",
        spec={"x": 1}, status="pending",
    )
    new_v = await repo.update_status_cas(
        task_id="t1", expected_version=1, status="running",
    )
    assert new_v == 2
    with pytest.raises(StateVersionConflict):
        await repo.update_status_cas(
            task_id="t1", expected_version=1, status="ok",
        )


@pytest.mark.asyncio
async def test_state_repo_idempotency():
    from agent.orchestrator.state_repo import reset_default_repo
    repo = reset_default_repo()
    row1 = await repo.save_task(
        task_id="t1", parent_run_id="r1", parent_task_id=None,
        correlation_id="c1", idempotency_token="idem-1", depth=1,
        task_type="plan", priority="normal",
        spec={"x": 1}, status="pending",
    )
    row2 = await repo.save_task(
        task_id="t1", parent_run_id="r1", parent_task_id=None,
        correlation_id="c1", idempotency_token="idem-1", depth=1,
        task_type="plan", priority="normal",
        spec={"x": 1}, status="pending",
    )
    assert row1["task_id"] == row2["task_id"]


@pytest.mark.asyncio
async def test_state_repo_dlq_lifecycle():
    from agent.orchestrator.state_repo import reset_default_repo
    repo = reset_default_repo()
    await repo.push_dlq(
        task_id="t1", correlation_id="c1",
        idempotency_token="idem-1",
        payload={"x": 1}, last_error="nope", attempts=3,
    )
    open_items = await repo.list_dlq(state="open")
    assert len(open_items) == 1
    ok = await repo.mark_dlq(task_id="t1", state="closed", note="manual")
    assert ok
    closed = await repo.list_dlq(state="closed")
    assert len(closed) == 1


@pytest.mark.asyncio
async def test_state_repo_metrics_summary():
    from agent.orchestrator.state_repo import reset_default_repo
    repo = reset_default_repo()
    await repo.record_metric(metric="latency_ms", value=100, task_id="t")
    await repo.record_metric(metric="latency_ms", value=200, task_id="t")
    summary = await repo.metric_summary("latency_ms")
    assert summary["count"] == 2
    assert summary["avg"] == 150


# ---- queue ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_priority_order():
    from agent.orchestrator.queue import reset_default_queue
    q = reset_default_queue()
    await q.enqueue(task_id="low1", idempotency_token="l1", payload={}, priority="low")
    await q.enqueue(task_id="n1", idempotency_token="n1", payload={}, priority="normal")
    await q.enqueue(task_id="h1", idempotency_token="h1", payload={}, priority="high")
    items = []
    for _ in range(3):
        items.append(await q.dequeue(timeout=1.0))
    assert [i.task_id for i in items] == ["h1", "n1", "low1"]


@pytest.mark.asyncio
async def test_queue_idempotency_dedup():
    from agent.orchestrator.queue import reset_default_queue
    q = reset_default_queue()
    a = await q.enqueue(task_id="t1", idempotency_token="idem", payload={})
    b = await q.enqueue(task_id="t1", idempotency_token="idem", payload={})
    assert a is True and b is False
    assert q.stats()["dedup_hits"] == 1


@pytest.mark.asyncio
async def test_queue_close_wakes_waiter():
    from agent.orchestrator.queue import reset_default_queue
    q = reset_default_queue()

    async def waiter():
        return await q.dequeue(timeout=2.0)
    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    await q.close()
    item = await asyncio.wait_for(task, timeout=1.0)
    assert item is None  # 关闭 + 空 → None


@pytest.mark.asyncio
async def test_queue_reopen_and_forget():
    from agent.orchestrator.queue import reset_default_queue
    q = reset_default_queue()
    await q.enqueue(task_id="t", idempotency_token="x", payload={})
    q.forget("x")
    await q.close()
    await q.reopen()
    ok = await q.enqueue(task_id="t", idempotency_token="x", payload={})
    assert ok is True


# ---- audit_bridge ---------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_bridge_log_and_replay(tmp_path, monkeypatch):
    """audit 写库 + replay 读回 correlation_id='c1'。"""
    audit_path = tmp_path / "audit.sqlite"
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(audit_path))
    from agent.config import settings
    monkeypatch.setattr(settings, "audit_db_path", str(audit_path))
    from agent.audit import store as audit_store_mod
    from agent.orchestrator import audit_bridge
    await audit_store_mod.audit(
        "noop", {}, run_id="r1", correlation_id="c1",
        actor_type="sub_agent", event_type="sub_agent_spawn",
        task_id="t1", parent_task_id=None, db_path=str(audit_path),
    )
    events = await audit_bridge.replay_tree("c1", db_path=str(audit_path))
    assert len(events) == 1
    assert events[0]["event_type"] == "sub_agent_spawn"


@pytest.mark.asyncio
async def test_audit_bridge_build_tree_parent_child():
    from agent.orchestrator.audit_bridge import build_tree
    events = [
        {"task_id": "p1", "parent_task_id": None, "event_type": "spawn", "payload": {}},
        {"task_id": "c1", "parent_task_id": "p1", "event_type": "spawn", "payload": {}},
        {"task_id": "c2", "parent_task_id": "p1", "event_type": "spawn", "payload": {}},
        {"task_id": "gc1", "parent_task_id": "c1", "event_type": "spawn", "payload": {}},
    ]
    tree = build_tree(events)
    assert len(tree) == 1
    p1 = tree[0]
    assert p1["task_id"] == "p1"
    assert len(p1["children"]) == 2


# ---- eval_collector -------------------------------------------------------


def test_eval_collector_record_and_snapshot():
    from agent.orchestrator.eval_collector import EvalCollector
    c = EvalCollector()
    c.record_dispatch(local_only=True)
    c.record_context(compression_ratio=0.6, required_kept=True)
    c.record_validation(ok=True)
    c.record_result(status="ok", latency_ms=100, attempts=1)
    snap = c.snapshot()
    assert snap["dispatched"] == 1
    assert snap["local_only_forced"] == 1
    assert snap["success_rate"] == 1.0
    assert snap["judge"]["is_ci_gate"] is False
    assert "p50_ms" in snap and "p99_ms" in snap
    assert isinstance(snap["thresholds"], dict)


def test_eval_collector_violations_below_threshold():
    from agent.orchestrator.eval_collector import EvalCollector
    c = EvalCollector()
    c.record_dispatch()
    c.record_validation(ok=False)  # 让 validation_pass_rate < 0.95
    snap = c.snapshot()
    assert "validation_pass_rate<0.95" in snap["violations"]


def test_eval_collector_judge_sampling_deterministic():
    from agent.orchestrator.eval_collector import EvalCollector
    c = EvalCollector()
    rate = 0.1
    hits = [c.should_judge(sample_rate=rate) for _ in range(20)]
    # 第 1/11 个是 True（counter=0 和 10 命中）
    assert hits[0] is True
    assert hits[10] is True
    assert hits[1] is False


def test_eval_collector_judge_parse_json_and_regex():
    from agent.orchestrator.eval_collector import _parse_judge_output
    score, _ = _parse_judge_output('{"score": 4, "reason": "ok"}')
    assert score == 4
    score, _ = _parse_judge_output("看了一下，给 3 分")
    assert score == 3
    score, reason = _parse_judge_output("garbage")
    assert score == 0
    assert "garbage" in reason


@pytest.mark.asyncio
async def test_eval_collector_judge_report_no_caller():
    from agent.orchestrator.eval_collector import judge_report
    v = await judge_report(
        task_id="t", task_type="plan",
        task_description="x", summary="y",
    )
    assert v.sampled is False
    assert v.error == "no judge_caller"


@pytest.mark.asyncio
async def test_eval_collector_judge_report_with_caller():
    from agent.orchestrator.eval_collector import judge_report, EvalCollector
    c = EvalCollector()

    async def fake_caller(prompt: str) -> str:
        return '{"score": 5, "reason": "ok"}'
    v = await judge_report(
        task_id="t", task_type="plan",
        task_description="x", summary="y",
        judge_caller=fake_caller, collector=c,
    )
    assert v.sampled is True and v.score == 5
    assert c.snapshot()["judge"]["samples"] == 1


# ---- hitl_bridge V1.5 -----------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_bridge_wait_for_user_approve(monkeypatch):
    from agent.orchestrator import hitl_bridge as hb
    hb.reset_default_hitl_bridge()

    # fake start_approval + check_decision
    async def fake_start(approval_id, plan, timeout_sec):
        return None
    async def fake_check(approval_id):
        return "approve"
    async def fake_cleanup(approval_id):
        return None

    import agent.graph.interrupt as gi
    monkeypatch.setattr(gi, "start_approval", fake_start)
    monkeypatch.setattr(gi, "check_decision", fake_check)
    monkeypatch.setattr(gi, "cleanup_approval", fake_cleanup)

    decision = await hb.get_default_hitl_bridge().request_approval(
        sub_agent_id="s", parent_run_id="r",
        operation="UPDATE x SET a=1", target="db",
        risk_level="high", wait_for_user=True, timeout_sec=2,
    )
    assert decision.approved is True
    assert decision.decided_by == "user"


@pytest.mark.asyncio
async def test_hitl_bridge_wait_for_user_timeout(monkeypatch):
    from agent.orchestrator import hitl_bridge as hb
    hb.reset_default_hitl_bridge()
    import agent.graph.interrupt as gi

    async def fake_start(approval_id, plan, timeout_sec):
        return None
    async def fake_check(approval_id):
        return None  # 永远没决策
    async def fake_cleanup(approval_id):
        return None

    monkeypatch.setattr(gi, "start_approval", fake_start)
    monkeypatch.setattr(gi, "check_decision", fake_check)
    monkeypatch.setattr(gi, "cleanup_approval", fake_cleanup)

    decision = await hb.get_default_hitl_bridge().request_approval(
        sub_agent_id="s", parent_run_id="r",
        operation="DROP TABLE x", target="db",
        risk_level="critical", wait_for_user=True, timeout_sec=0.2,
    )
    assert decision.decision == "reject"
    assert decision.timed_out is True
    assert decision.decided_by == "timeout"


@pytest.mark.asyncio
async def test_hitl_bridge_emit_approval_event(monkeypatch):
    from agent.orchestrator import hitl_bridge as hb
    from agent.orchestrator import events as evt
    evt.flush_orchestrator_events()
    hb.reset_default_hitl_bridge()
    import agent.graph.interrupt as gi

    async def fake_start(approval_id, plan, timeout_sec):
        return None
    async def fake_check(approval_id):
        return "approve"
    async def fake_cleanup(approval_id):
        return None

    monkeypatch.setattr(gi, "start_approval", fake_start)
    monkeypatch.setattr(gi, "check_decision", fake_check)
    monkeypatch.setattr(gi, "cleanup_approval", fake_cleanup)

    await hb.get_default_hitl_bridge().request_approval(
        sub_agent_id="s", parent_run_id="r",
        operation="UPDATE y", target="db",
        risk_level="medium", wait_for_user=True, timeout_sec=2,
    )
    evs = await evt.consume_orchestrator_events()
    kinds = [k for k, _ in evs]
    assert "approval" in kinds


# ---- observability --------------------------------------------------------


@pytest.mark.asyncio
async def test_observability_scrub_pii(tmp_path):
    from agent.orchestrator.observability import (
        reset_default_logger, _scrub_value,
    )
    reset_default_logger(log_dir=tmp_path)
    from agent.orchestrator.observability import get_default_logger
    logger = get_default_logger()
    await logger.log_event(
        event_type="sub_agent_spawn",
        correlation_id="c1",
        task_id="t1",
        payload={
            "password": "hunter2",          # 字段脱敏
            "phone": "13800001111",         # 正则脱敏
            "ok": "hi",
        },
    )
    log_file = next(tmp_path.glob("orchestrator-*.jsonl"))
    line = log_file.read_text(encoding="utf-8").strip()
    assert "[REDACTED]" in line or "redacted" in line.lower()
    assert "hunter2" not in line
    assert "13800001111" not in line
    await logger.aclose()


# ---- orchestrator V1.5 集成 ------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_dispatch_and_run():
    from agent.orchestrator.orchestrator import Orchestrator
    from agent.orchestrator.spec import SubAgentSpec
    from agent.orchestrator.state_repo import reset_default_repo
    from agent.orchestrator.queue import reset_default_queue
    from agent.orchestrator.eval_collector import reset_default_collector
    from agent.orchestrator.context_strategy import reset_default_pool

    reset_default_repo()
    reset_default_queue()
    reset_default_collector()
    reset_default_pool()

    # mock LMRouter
    router = MagicMock()
    router.route = AsyncMock(return_value="这是子 Agent 的结构化摘要")
    orch = Orchestrator(llm_router=router)
    orch.set_tenant("t-test")

    spec = SubAgentSpec(
        sub_agent_id="sub-v15-1", parent_run_id="run-v15-1", depth=1,
        task_type="plan", task_description="请总结本月 OKR",
        input_payload={
            "file": "okr.md",
            "idempotency_token": "idem-v15-1",
            "correlation_id": "run-v15-1:sub-v15-1",
        },
    )
    task_id = await orch.dispatch(spec, priority="normal")
    assert task_id == "sub-v15-1"

    # 队列里现在有 1 条
    assert orch.task_queue.qsize() == 1
    reports = await orch.run_until_drained(timeout=5.0)
    assert len(reports) == 1
    assert reports[0].status.value == "ok"
    # repo 状态应该是 ok
    row = await orch.repo.get_task("sub-v15-1")
    assert row["status"] == "ok"
    # 评估指标
    snap = orch.collector.snapshot()
    assert snap["dispatched"] == 1
    assert snap["succeeded"] == 1


@pytest.mark.asyncio
async def test_orchestrator_dlq_on_repeated_failure():
    from agent.orchestrator.orchestrator import Orchestrator
    from agent.orchestrator.spec import SubAgentSpec
    from agent.orchestrator.state_repo import reset_default_repo
    from agent.orchestrator.queue import reset_default_queue
    from agent.orchestrator.eval_collector import reset_default_collector
    from agent.orchestrator.context_strategy import reset_default_pool
    from agent.orchestrator.worker_pool import WorkerPool

    reset_default_repo()
    reset_default_queue()
    reset_default_collector()
    reset_default_pool()

    router = MagicMock()
    router.route = AsyncMock(side_effect=RuntimeError("nope"))
    # 强制 max_attempts=2 加快测试
    pool = WorkerPool(concurrency=1, retry_base_delay_s=0.01, max_attempts=2)
    orch = Orchestrator(llm_router=router, worker_pool=pool)
    orch.set_tenant("t-test")

    spec = SubAgentSpec(
        sub_agent_id="sub-v15-2", parent_run_id="run-v15-2", depth=1,
        task_type="plan", task_description="失败",
        input_payload={"idempotency_token": "idem-v15-2"},
    )
    await orch.dispatch(spec)
    reports = await orch.run_until_drained(timeout=10.0)
    assert len(reports) == 1
    assert reports[0].status.value == "dlq"
    dlq = await orch.repo.list_dlq(state="open")
    assert len(dlq) == 1


@pytest.mark.asyncio
async def test_orchestrator_cancel_all_drains_within_1s():
    """cancel_all ≤ 1s 内能通知 worker 停。"""
    from agent.orchestrator.orchestrator import Orchestrator
    from agent.orchestrator.spec import SubAgentSpec
    from agent.orchestrator.state_repo import reset_default_repo
    from agent.orchestrator.queue import reset_default_queue
    from agent.orchestrator.eval_collector import reset_default_collector
    from agent.orchestrator.context_strategy import reset_default_pool
    import time

    reset_default_repo()
    reset_default_queue()
    reset_default_collector()
    reset_default_pool()

    # mock router 慢一些（≥ 0.3s）—— 让 cancel 有机会触发
    async def slow_route(task, prompt):
        await asyncio.sleep(0.3)
        return "ok"

    router = MagicMock()
    router.route = slow_route

    orch = Orchestrator(llm_router=router)
    orch.set_tenant("t-test")

    for i in range(3):
        spec = SubAgentSpec(
            sub_agent_id=f"sub-cancel-{i}", parent_run_id="run-cancel", depth=1,
            task_type="plan", task_description="x",
            input_payload={"idempotency_token": f"cancel-{i}"},
        )
        await orch.dispatch(spec)

    # 立即 cancel_all
    t0 = time.monotonic()
    orch.cancel_all()
    elapsed_cancel = time.monotonic() - t0
    assert elapsed_cancel < 1.0  # 验收：取消传播 ≤ 1s
    # 队列应被 close → dequeue 返 None → run_until_drained 退出
    reports = await orch.run_until_drained(timeout=0.5)
    # 因为队列被 close，空队列返空列表也是合规的
    assert isinstance(reports, list)
    # 但 cancel_event 必须被 set
    assert orch.cancel_event.is_set() is True


# ---- API 路由顺序 ----------------------------------------------------------


@pytest.mark.asyncio
async def test_api_metrics_endpoint():
    from fastapi.testclient import TestClient
    from agent.orchestrator import api as api_mod
    from agent.orchestrator.orchestrator import get_orchestrator
    get_orchestrator()  # 触发单例初始化

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)
    resp = client.get("/orchestrator/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "dispatched" in data
    assert "thresholds" in data
    assert "judge" in data


@pytest.mark.asyncio
async def test_api_tree_stats():
    from fastapi.testclient import TestClient
    from agent.orchestrator import api as api_mod
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)
    resp = client.get("/orchestrator/tree/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_depth"] == 2
    assert data["max_total_nodes"] == 30


@pytest.mark.asyncio
async def test_api_queue_stats():
    from fastapi.testclient import TestClient
    from agent.orchestrator import api as api_mod
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)
    resp = client.get("/orchestrator/queue/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "by_priority" in data


@pytest.mark.asyncio
async def test_api_dlq_and_routes_in_correct_order():
    """字面量路径必须在 /{sub_agent_id} 通配符之前。"""
    from fastapi.testclient import TestClient
    from agent.orchestrator import api as api_mod
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)
    resp = client.get("/orchestrator/dlq?state=open&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "open"
    assert isinstance(data["items"], list)
