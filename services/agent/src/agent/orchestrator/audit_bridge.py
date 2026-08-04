"""orchestrator.audit_bridge —— Phase 12 V1.5 子 Agent 决策审计 + 轨迹回放。

铁律 8「决策全审计」落地：
    子 Agent 的每一次派发 / 进度 / 完成 / 重试 / DLQ / 取消 / HITL / Judge 都是
    `audit.sqlite` 的一条事件，带 `correlation_id` 可串联整棵决策树。

复用 Phase 1 审计表（CLAUDE.md §6 双 schema）：
    V1.5 已在 `audit` 表加 5 列（correlation_id / actor_type / event_type /
    task_id / parent_task_id），Python 与 Rust schema 严格镜像。本模块只调
    `agent.audit.store.audit()`，不自建表。

轨迹回放（设计文档 §3.3）：
    `replay_tree(correlation_id)` 按时间序返回整棵决策树的事件列表，
    `build_tree(events)` 把扁平事件折成父子结构，供前端渲染 / 事后复盘。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from agent.audit.store import audit
from agent.config import settings

logger = logging.getLogger(__name__)

ACTOR_SUB_AGENT = "sub_agent"

# 6 类事件（实现文档 §7.3）+ V1.5 追加 hitl / judge / closed
EVENT_SPAWN = "sub_agent_spawn"
EVENT_PROGRESS = "sub_agent_progress"
EVENT_DONE = "sub_agent_done"
EVENT_RETRY = "sub_agent_retry"
EVENT_DLQ = "sub_agent_dlq"
EVENT_CANCEL = "sub_agent_cancel"
EVENT_CLOSED = "sub_agent_closed"
EVENT_REQUEUED = "sub_agent_requeued"
EVENT_HITL_REQUESTED = "sub_agent_hitl_requested"
EVENT_HITL_DECIDED = "sub_agent_hitl_decided"
EVENT_JUDGE = "sub_agent_judge"
EVENT_QUEUED = "sub_agent_queued"

ALL_EVENT_TYPES: tuple[str, ...] = (
    EVENT_SPAWN, EVENT_PROGRESS, EVENT_DONE, EVENT_RETRY, EVENT_DLQ,
    EVENT_CANCEL, EVENT_CLOSED, EVENT_REQUEUED,
    EVENT_HITL_REQUESTED, EVENT_HITL_DECIDED, EVENT_JUDGE, EVENT_QUEUED,
)


async def log_event(
    event_type: str,
    *,
    correlation_id: str,
    task_id: str | None = None,
    parent_task_id: str | None = None,
    run_id: str | None = None,
    actor_type: str = ACTOR_SUB_AGENT,
    payload: dict[str, Any] | None = None,
) -> None:
    """写一条子 Agent 审计事件（失败不抛异常 —— 审计不阻塞主流程）。"""
    if event_type not in ALL_EVENT_TYPES:
        logger.warning("[audit_bridge] 未登记的 event_type=%s（仍写入）", event_type)
    try:
        await audit(
            event_type,
            payload or {},
            run_id=run_id,
            correlation_id=correlation_id,
            actor_type=actor_type,
            event_type=event_type,
            task_id=task_id,
            parent_task_id=parent_task_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[audit_bridge] audit 写入失败 event=%s err=%s", event_type, exc)


def make_correlation_id(parent_run_id: str, root_task_id: str | None = None) -> str:
    """一棵决策树共享的 correlation_id。

    根子 Agent：`{run_id}:{sub_agent_id}`；嵌套子 Agent 继承父的 correlation_id。
    """
    return f"{parent_run_id}:{root_task_id}" if root_task_id else parent_run_id


# ---- 轨迹回放 --------------------------------------------------------------


async def replay_tree(
    correlation_id: str,
    *,
    db_path: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """按时间序读取整棵决策树的审计事件。

    Returns: 每条含 ts / event_type / actor_type / task_id / parent_task_id / payload
    """
    path = db_path or settings.audit_db_path
    if not Path(path).exists():
        return []
    db = await aiosqlite.connect(path)
    try:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute(
                "SELECT id, ts, action, event_type, actor_type, task_id, "
                "parent_task_id, run_id, payload FROM audit "
                "WHERE correlation_id = ? ORDER BY id ASC LIMIT ?",
                (correlation_id, limit),
            )
        except aiosqlite.OperationalError:
            # 老库还没 ALTER TABLE（audit.store 首写时会补列）
            return []
        rows = await cur.fetchall()
    finally:
        await db.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
        except (json.JSONDecodeError, TypeError):
            payload = {"_raw": row["payload"]}
        out.append({
            "id": row["id"],
            "ts": row["ts"],
            "event_type": row["event_type"] or row["action"],
            "actor_type": row["actor_type"],
            "task_id": row["task_id"],
            "parent_task_id": row["parent_task_id"],
            "run_id": row["run_id"],
            "payload": payload,
        })
    return out


def build_tree(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把扁平事件折成父子树（按 task_id / parent_task_id）。

    返回根节点列表；每个节点：
        {task_id, parent_task_id, events: [...], children: [...]}
    """
    nodes: dict[str, dict[str, Any]] = {}
    orphan_events: list[dict[str, Any]] = []

    for evt in events:
        tid = evt.get("task_id")
        if not tid:
            orphan_events.append(evt)
            continue
        node = nodes.setdefault(tid, {
            "task_id": tid,
            "parent_task_id": evt.get("parent_task_id"),
            "events": [],
            "children": [],
        })
        if evt.get("parent_task_id") and not node["parent_task_id"]:
            node["parent_task_id"] = evt["parent_task_id"]
        node["events"].append(evt)

    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent_id = node["parent_task_id"]
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)

    if orphan_events:
        roots.append({
            "task_id": None,
            "parent_task_id": None,
            "events": orphan_events,
            "children": [],
        })
    return roots


async def replay_summary(
    correlation_id: str, *, db_path: str | None = None
) -> dict[str, Any]:
    """回放摘要：事件计数 + 涉及的 task 数 + 是否有 DLQ / 取消。"""
    events = await replay_tree(correlation_id, db_path=db_path)
    counts: dict[str, int] = {}
    for evt in events:
        key = str(evt.get("event_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    task_ids = {e["task_id"] for e in events if e.get("task_id")}
    return {
        "correlation_id": correlation_id,
        "event_count": len(events),
        "task_count": len(task_ids),
        "by_event_type": counts,
        "has_dlq": counts.get(EVENT_DLQ, 0) > 0,
        "has_cancel": counts.get(EVENT_CANCEL, 0) > 0,
        "replayable": len(events) > 0,
    }


__all__ = [
    "ACTOR_SUB_AGENT",
    "ALL_EVENT_TYPES",
    "EVENT_SPAWN", "EVENT_PROGRESS", "EVENT_DONE", "EVENT_RETRY", "EVENT_DLQ",
    "EVENT_CANCEL", "EVENT_CLOSED", "EVENT_REQUEUED",
    "EVENT_HITL_REQUESTED", "EVENT_HITL_DECIDED", "EVENT_JUDGE",
    "log_event",
    "make_correlation_id",
    "replay_tree",
    "replay_summary",
    "build_tree",
]
