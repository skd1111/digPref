"""Phase 14 V0 · 图像处理事件 emit 机制。

仿照 builtin.events 设计：进程内 deque + asyncio 锁 + 3 事件常量。
V1 接力时由 api.py 在每个处理前后 emit；stream.py::_drain_image_events 拉取转 SSE。

事件类型（与 graph/stream.py::_CHANNEL_BY_KIND 严格一致）：
  - image_processing_started
  - image_processing_done
  - image_processing_error
"""

from __future__ import annotations

import asyncio
from collections import deque

# 进程内事件队列
_img_events: deque[tuple[str, dict]] = deque()
_img_lock = asyncio.Lock()


# ---- 公开事件常量 --------------------------------------------------------

EVT_IMG_PROCESSING_STARTED = "image_processing_started"
EVT_IMG_PROCESSING_DONE = "image_processing_done"
EVT_IMG_PROCESSING_ERROR = "image_processing_error"


async def emit_event(kind: str, payload: dict) -> None:
    async with _img_lock:
        _img_events.append((kind, payload))


def emit_event_sync(kind: str, payload: dict) -> None:
    """同步版 emit（to_thread 内调用）。"""
    _img_events.append((kind, payload))


async def consume_events(timeout_s: float = 0.0) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    async with _img_lock:
        while _img_events:
            events.append(_img_events.popleft())
    return events


async def flush_events() -> int:
    async with _img_lock:
        count = len(_img_events)
        _img_events.clear()
    return count
