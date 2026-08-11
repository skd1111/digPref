"""Phase 2B V0 · SSH 会话事件 emit 机制（仿照 builtin.events / image_processing.events）。"""

from __future__ import annotations

import asyncio
from collections import deque

_ssh_events: deque[tuple[str, dict]] = deque()
_ssh_lock = asyncio.Lock()


# 公开事件常量（与 graph/stream.py::_CHANNEL_BY_KIND 严格一致）
EVT_SSH_CONNECTED = "ssh_connected"
EVT_SSH_DISCONNECTED = "ssh_disconnected"
EVT_SSH_COMMAND_DONE = "ssh_command_done"
EVT_SSH_ERROR = "ssh_error"


async def emit_event(kind: str, payload: dict) -> None:
    async with _ssh_lock:
        _ssh_events.append((kind, payload))


def emit_event_sync(kind: str, payload: dict) -> None:
    _ssh_events.append((kind, payload))


async def consume_events(timeout_s: float = 0.0) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    async with _ssh_lock:
        while _ssh_events:
            events.append(_ssh_events.popleft())
    return events


async def flush_events() -> int:
    async with _ssh_lock:
        count = len(_ssh_events)
        _ssh_events.clear()
    return count
