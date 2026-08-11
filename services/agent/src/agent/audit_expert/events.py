"""Phase 5 V0 · 审核专家事件 emit 机制。"""

from __future__ import annotations

import asyncio
from collections import deque

_audit_events: deque[tuple[str, dict]] = deque()
_audit_lock = asyncio.Lock()


# 公开事件常量（与 graph/stream.py::_CHANNEL_BY_KIND 严格一致）
EVT_AUDIT_TASK_PENDING = "audit_task_pending"
EVT_AUDIT_TASK_DECIDED = "audit_task_decided"
EVT_AUDIT_EVIDENCE_ADDED = "audit_evidence_added"
EVT_AUDIT_COMPLIANCE_DONE = "audit_compliance_done"


async def emit_event(kind: str, payload: dict) -> None:
    async with _audit_lock:
        _audit_events.append((kind, payload))


def emit_event_sync(kind: str, payload: dict) -> None:
    _audit_events.append((kind, payload))


async def consume_events(timeout_s: float = 0.0) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    async with _audit_lock:
        while _audit_events:
            events.append(_audit_events.popleft())
    return events


async def flush_events() -> int:
    async with _audit_lock:
        count = len(_audit_events)
        _audit_events.clear()
    return count
