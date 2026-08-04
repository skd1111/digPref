"""Phase 7 V0 · 数据专家事件 emit 机制。

SSE 三处同步（CLAUDE.md §4）：
  - Python: graph/stream.py::_CHANNEL_BY_KIND
  - Rust:   stream/sse_bridge.rs::channel
  - TS:     ipc/events.ts::EVT
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any


_data_events: deque[tuple[str, dict]] = deque()
_data_lock = asyncio.Lock()


# 公开事件常量（与 graph/stream.py::_CHANNEL_BY_KIND 严格一致）
EVT_DATA_QUERY_RESULT = "data_query_result"
EVT_DATA_PYTHON_RESULT = "data_python_result"
EVT_DATA_CHART_READY = "data_chart_ready"
EVT_DATA_EXPORT_DONE = "data_export_done"


async def emit_event(kind: str, payload: dict) -> None:
    async with _data_lock:
        _data_events.append((kind, payload))


def emit_event_sync(kind: str, payload: dict) -> None:
    _data_events.append((kind, payload))


async def consume_events(timeout_s: float = 0.0) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    async with _data_lock:
        while _data_events:
            events.append(_data_events.popleft())
    return events


async def flush_events() -> int:
    async with _data_lock:
        count = len(_data_events)
        _data_events.clear()
    return count
