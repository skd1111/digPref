"""evolution.events —— Phase 19 V0 SSE 事件 emit 机制。

设计（参照 agent.skills.events / agent.biznav.events 风格）：
    - 进程级 deque + 异步消费
    - `emit_evolution_event(kind, payload)` 写入；`consume_evolution_events()` 拉出
    - graph/stream.py 流循环消费后通过 SSE 推到前端

CLAUDE.md §4 SSE 三处同步：
    - Python (本文件) → graph/stream.py → Rust stream/sse_bridge.rs::channel
      → TS ipc/events.ts::EVT
    - 通道名严格一致：`evolution_insight_created`
"""

from __future__ import annotations

from collections import deque
from typing import Any

# 进程级 deque（maxlen 防无界增长）
_evolution_event_queue: deque[tuple[str, dict[str, Any]]] = deque(maxlen=1000)

# 事件通道名常量（与 sse_bridge.rs::channel + events.ts::EVT 同步）
EVT_EVOLUTION_INSIGHT_CREATED: str = "evolution_insight_created"
EVT_SKILL_DRAFT_READY: str = "skill_draft_ready"
EVT_EVOLUTION_EXPERIMENT_DONE: str = "evolution_experiment_done"

_VALID_KINDS = frozenset(
    {EVT_EVOLUTION_INSIGHT_CREATED, EVT_SKILL_DRAFT_READY, EVT_EVOLUTION_EXPERIMENT_DONE}
)


def emit_evolution_event(kind: str, payload: dict[str, Any]) -> None:
    """把进化事件写入进程内 deque（供 SSE 流循环消费下发）。"""
    if kind not in _VALID_KINDS:
        return
    _evolution_event_queue.append((kind, payload))


async def consume_evolution_events(timeout_s: float = 0.0) -> list[tuple[str, dict[str, Any]]]:
    """拉出当前 deque 中的所有事件（不阻塞）。

    graph/stream.py 流循环 + 收尾各调一次，确保 buffered 事件全部推完。
    """
    events: list[tuple[str, dict[str, Any]]] = []
    while True:
        try:
            kind, payload = _evolution_event_queue.popleft()
            events.append((kind, payload))
        except IndexError:
            break
    return events


def flush_evolution_events() -> None:
    """清空事件队列（仅测试用）。"""
    _evolution_event_queue.clear()
