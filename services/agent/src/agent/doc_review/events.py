"""文档审核 SSE 事件（in-process deque，与 audit_expert.events 同构）。"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

_doc_review_events: deque[tuple[str, dict[str, Any]]] = deque()
_doc_review_lock = asyncio.Lock()


EVT_DOC_REVIEW_STARTED = "doc_review_started"
EVT_DOC_REVIEW_CLASSIFIED = "doc_review_classified"
EVT_DOC_REVIEW_FINDINGS_READY = "doc_review_findings_ready"
EVT_DOC_REVIEW_FAILED = "doc_review_failed"


async def emit_event(kind: str, payload: dict[str, Any]) -> None:
    async with _doc_review_lock:
        _doc_review_events.append((kind, payload))


def emit_event_sync(kind: str, payload: dict[str, Any]) -> None:
    _doc_review_events.append((kind, payload))


async def consume_events(timeout_s: float = 0.0) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    async with _doc_review_lock:
        while _doc_review_events:
            events.append(_doc_review_events.popleft())
    return events
