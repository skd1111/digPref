"""skills.events —— Phase 2D V1 SSE 事件 emit 机制。

设计（参照 agent.biznav.events 风格）：
    - 进程级 deque + asyncio 锁
    - `emit_skill_event(kind, payload)` 写入；`consume_skill_events()` 拉出
    - graph/stream.py 流循环消费后通过 SSE 推到前端

CLAUDE.md §4 SSE 三处同步（V1.3）：
    - Python (本文件) → graph/stream.py → Rust stream/sse_bridge.rs::channel
      → TS ipc/events.ts::EVT
    - 通道名严格一致：`skill_matched`
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

# 进程级 deque + 异步锁
_skill_event_queue: deque[tuple[str, dict]] = deque(maxlen=1000)
_skill_event_lock = asyncio.Lock()


# ---- 1 个事件通道名常量（与 sse_bridge.rs::channel + events.ts::EVT 同步）----
EVT_SKILL_MATCHED: str = "skill_matched"


def emit_skill_event(kind: str, payload: dict[str, Any]) -> None:
    """把 skill 事件写入进程内 deque。

    `kind` 必须是 `EVT_SKILL_MATCHED` 之一（未来扩展时再扩 frozenset）。
    """
    _skill_event_queue.append((kind, payload))


async def consume_skill_events(timeout_s: float = 0.0) -> list[tuple[str, dict]]:
    """异步拉出当前 deque 中的所有事件（不阻塞）。

    graph/stream.py 流循环 + finally 各调一次，确保 buffered 事件全部推完。

    测试用：调 `flush_skill_events()` 清空队列。
    """
    events: list[tuple[str, dict]] = []
    while True:
        try:
            kind, payload = _skill_event_queue.popleft()
            events.append((kind, payload))
        except IndexError:
            break
    return events


def flush_skill_events() -> None:
    """清空事件队列（仅测试用）。"""
    _skill_event_queue.clear()
