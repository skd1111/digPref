"""orchestrator.api —— Phase 12 V1.5 FastAPI 路由。

V0（保留 / 行为不变）：
    POST /orchestrator/spawn                 派生一个子 Agent
    GET  /orchestrator/list                  列出所有子 Agent
    GET  /orchestrator/{sub_agent_id}        单个 sub_agent 详情
    POST /orchestrator/{sub_agent_id}/cancel 取消（标记 CANCELLED）
    GET  /orchestrator/tree/stats            派生树统计

V1（保留 / 行为不变）：
    内部使用 WorkerPool / 锁 / Token Bucket / HITL bridge

V1.5（新增）：
    POST /orchestrator/dispatch              异步派发（入队即返，不等结果）
    POST /orchestrator/run_until_drained     消费队列直到空/取消
    GET  /orchestrator/dlq                   列出 DLQ（默认 state=open）
    POST /orchestrator/dlq/{task_id}/requeue DLQ 重新入队
    POST /orchestrator/dlq/{task_id}/close   关闭 DLQ 条目
    GET  /orchestrator/metrics               评测指标快照
    GET  /orchestrator/queue/stats           队列堆积统计
    GET  /orchestrator/replay/{correlation_id}  决策树回放
    POST /orchestrator/cancel_all            全局取消

注意路由顺序（FastAPI 按注册顺序匹配）：
    字面量路径 `/list` / `/dispatch` / `/run_until_drained` / `/dlq` / `/metrics`
    `/queue/stats` / `/cancel_all` / `/tree/stats` 都必须在 `/{sub_agent_id}` 通配符
    之前，否则会被吞掉。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from agent.orchestrator import audit_bridge
from agent.orchestrator.orchestrator import get_orchestrator
from agent.orchestrator.spec import SubAgentSpec
from agent.orchestrator.tree_guard import TreeLimitExceeded

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


# ---- V0/V1 兼容 ----------------------------------------------------------


@router.post("/spawn")
async def spawn_sub_agent(spec: SubAgentSpec) -> dict:
    """派生子 Agent（同步阻塞直到完成）—— V0 兼容。"""
    orch = get_orchestrator()
    try:
        report = await orch.spawn(spec)
    except TreeLimitExceeded as e:
        raise HTTPException(
            status_code=400,
            detail=f"派生树硬上限触发：{e.reason} 当前={e.current} 上限={e.limit}",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("[orchestrator] spawn crashed: %s", e)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return report.model_dump(mode="json")


@router.get("/list")
async def list_sub_agents() -> dict:
    """列出所有 sub_agent 简要信息（优先从 StateRepo 持久层读）。"""
    orch = get_orchestrator()
    items: list[dict[str, Any]] = []
    try:
        rows = await orch.repo.list_tasks(limit=200)
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        items.append({
            "task_id": r.get("task_id"),
            "sub_agent_id": r.get("task_id"),
            "parent_run_id": r.get("parent_run_id"),
            "parent_sub_agent_id": r.get("parent_task_id"),
            "status": r.get("status"),
            "task_type": r.get("task_type"),
            "started_at": r.get("created_at"),
            "finished_at": r.get("updated_at"),
            "latency_ms": r.get("latency_ms", 0),
            "confidence": 0.0,
            "backend": r.get("backend"),
            "strategy": r.get("strategy"),
        })
    for r in orch.list_reports():
        if any(it.get("sub_agent_id") == r.sub_agent_id for it in items):
            continue
        items.append({
            "sub_agent_id": r.sub_agent_id,
            "parent_run_id": r.parent_run_id,
            "parent_sub_agent_id": r.parent_sub_agent_id,
            "status": r.status.value,
            "task_type": (r.state_delta.fields_added.get("task_type") if r.state_delta else None),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "latency_ms": r.latency_ms,
            "confidence": r.confidence,
        })
    return {
        "total_nodes": orch.total_nodes,
        "items": items,
    }


@router.get("/tree/stats")
async def tree_stats() -> dict:
    """派生树统计：用于前端 UI 展示（铁律 2 实时监控）。"""
    orch = get_orchestrator()
    return {
        "total_nodes": orch.total_nodes,
        "max_depth": 2,
        "max_total_nodes": 30,
        "headroom_depth": 2,
        "headroom_nodes": max(0, 30 - orch.total_nodes),
    }


@router.get("/queue/stats")
async def queue_stats() -> dict:
    """队列堆积统计（监控告警阈值 > 100）。"""
    orch = get_orchestrator()
    return orch.task_queue.stats()


@router.get("/metrics")
async def metrics() -> dict:
    """评测指标快照 + 验证阈值。"""
    orch = get_orchestrator()
    return orch.collector.snapshot()


# ---- V1.5 新增：dispatch / run_until_drained / DLQ / replay / cancel_all --


@router.post("/dispatch")
async def dispatch_sub_agent(spec: SubAgentSpec, priority: str = "normal") -> dict:
    """V1.5 异步派发：入队即返 task_id，不等结果。"""
    orch = get_orchestrator()
    try:
        task_id = await orch.dispatch(spec, priority=priority)
    except TreeLimitExceeded as e:
        raise HTTPException(
            status_code=400,
            detail=f"派生树硬上限触发：{e.reason} 当前={e.current} 上限={e.limit}",
        )
    return {"task_id": task_id, "status": "pending", "priority": priority}


@router.post("/run_until_drained")
async def run_until_drained(timeout: float = 60.0) -> dict:
    """V1.5 消费队列直到空 / 取消 / 超时。返回完成的 report 列表。"""
    orch = get_orchestrator()
    try:
        reports = await orch.run_until_drained(timeout=timeout)
    except Exception as e:  # noqa: BLE001
        logger.exception("[orchestrator] run_until_drained crashed: %s", e)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return {
        "completed": len(reports),
        "items": [r.model_dump(mode="json") for r in reports],
    }


@router.post("/cancel_all")
async def cancel_all() -> dict:
    """V1.5 取消传播（≤ 1s 唤醒所有 worker）。"""
    orch = get_orchestrator()
    t0 = time.monotonic()
    orch.cancel_all()
    return {"ok": True, "elapsed_ms": int((time.monotonic() - t0) * 1000)}


@router.get("/dlq")
async def list_dlq(state: str = "open", limit: int = 50) -> dict:
    """V1.5 DLQ 列表（默认 open；运维可切 closed 查历史）。"""
    orch = get_orchestrator()
    try:
        rows = await orch.repo.list_dlq(state=state, limit=limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return {"state": state, "count": len(rows), "items": rows}


@router.post("/dlq/{task_id}/requeue")
async def requeue_dlq(task_id: str, note: str | None = None) -> dict:
    """V1.5 DLQ 重新入队（注意：会从 queue 的幂等集里 forget token 避免死锁）。"""
    orch = get_orchestrator()
    try:
        item_row = await orch.repo.get_task(task_id)
        if item_row and item_row.get("idempotency_token"):
            orch.task_queue.forget(item_row["idempotency_token"])
        spec_dict = item_row.get("spec_json") if item_row else None
        if spec_dict:
            spec = SubAgentSpec.model_validate(json.loads(spec_dict))
            await orch.dispatch(spec)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"requeue dispatch 失败: {e}")
    ok = await orch.repo.mark_dlq(
        task_id=task_id, state="requeued", note=note, handled_by="api",
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"DLQ 不存在 task_id={task_id}")
    return {"ok": True, "task_id": task_id, "state": "requeued"}


@router.post("/dlq/{task_id}/close")
async def close_dlq(task_id: str, note: str | None = None) -> dict:
    orch = get_orchestrator()
    ok = await orch.repo.mark_dlq(
        task_id=task_id, state="closed", note=note, handled_by="api",
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"DLQ 不存在 task_id={task_id}")
    return {"ok": True, "task_id": task_id, "state": "closed"}


@router.get("/replay/{correlation_id}")
async def replay_correlation(correlation_id: str, limit: int = 1000) -> dict:
    """V1.5 决策树回放（按 correlation_id 串联）。"""
    events = await audit_bridge.replay_tree(correlation_id, limit=limit)
    tree = audit_bridge.build_tree(events)
    summary = await audit_bridge.replay_summary(correlation_id)
    return {
        "correlation_id": correlation_id,
        "summary": summary,
        "event_count": len(events),
        "events": events,
        "tree": tree,
    }


# ---- 单条路径（必须在通配符之后） ----------------------------------------


@router.get("/{sub_agent_id}")
async def get_sub_agent(sub_agent_id: str) -> dict:
    """获取单个 sub_agent 的完整 SubAgentReport。"""
    orch = get_orchestrator()
    report = orch.get_report(sub_agent_id)
    if report is not None:
        return report.model_dump(mode="json")
    row = await orch.repo.get_task(sub_agent_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"sub_agent_id={sub_agent_id} 不存在")
    if row.get("report_json"):
        try:
            return json.loads(row["report_json"])
        except json.JSONDecodeError:
            pass
    return row


@router.post("/{sub_agent_id}/cancel")
async def cancel_sub_agent(sub_agent_id: str) -> dict:
    """取消子 Agent（V0 占位：标记为 cancelled）。"""
    orch = get_orchestrator()
    ok = await orch.cancel(sub_agent_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"sub_agent_id={sub_agent_id} 已结束，无法取消",
        )
    return {"ok": True, "sub_agent_id": sub_agent_id}
