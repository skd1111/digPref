"""orchestrator.events —— Phase 12 V1.5 子 Agent SSE 事件 emit 机制。

设计（参照 biznav/events.py + builtin/events.py 的进程内 deque 风格）：
    - 进程级 deque：orchestrator.py / worker 重试 / hitl_bridge 等模块调
      `emit_orchestrator_event()` 写入；
    - `graph/stream.py::_drain_orchestrator_events()` 在流循环 + finally 各拉一次，
      转成 SSE `data:` 行推给前端。
    - deque 上限 2000 条防 memory leak（子 Agent 事件比 biznav 密集）。
    - CPython GIL 保证单个 append/popleft 原子；asyncio 单事件循环无数据竞争。

CLAUDE.md §4 SSE 三处同步：
    Python emit（本文件）→ graph/stream.py 转发 → Rust sse_bridge.rs::channel
    → TS ipc/events.ts::EVT

    通道名必须严格一致：
      - `sub_agent_spawn`     → agent://sub_agent_spawn
      - `sub_agent_progress`  → agent://sub_agent_progress
      - `sub_agent_done`      → agent://sub_agent_done

    另复用主图既有 `approval` 通道：子 Agent 写操作反向 HITL 时，
    hitl_bridge 通过本模块 emit `approval` 事件，前端 ApprovalCard 直接复用
    （CLAUDE.md §1：HITL 是脊梁，不重造审批通道）。
"""

from __future__ import annotations

from collections import deque
from typing import Any

# 进程级 deque（asyncio 单线程安全）
_orchestrator_event_queue: deque[tuple[str, dict]] = deque(maxlen=2000)


# ---- 事件通道名常量（与 sse_bridge.rs::channel + events.ts::EVT 同步）----
EVT_SUB_AGENT_SPAWN: str = "sub_agent_spawn"
EVT_SUB_AGENT_PROGRESS: str = "sub_agent_progress"
EVT_SUB_AGENT_DONE: str = "sub_agent_done"
# 复用主图审批通道（不新增）
EVT_APPROVAL: str = "approval"

_ALLOWED_KINDS = frozenset(
    {
        EVT_SUB_AGENT_SPAWN,
        EVT_SUB_AGENT_PROGRESS,
        EVT_SUB_AGENT_DONE,
        EVT_APPROVAL,
    }
)


def emit_orchestrator_event(kind: str, payload: dict[str, Any]) -> None:
    """把子 Agent 事件写入进程内 deque。

    `kind` 必须是 `_ALLOWED_KINDS` 之一，否则静默丢弃（防止拼错通道名后
    在 `graph/stream.py` 里被 `_CHANNEL_BY_KIND.get()` 吞掉而无从排查）。
    """
    if kind not in _ALLOWED_KINDS:
        raise ValueError(f"未知 orchestrator 事件通道: {kind!r}（允许：{sorted(_ALLOWED_KINDS)}）")
    _orchestrator_event_queue.append((kind, payload))


async def consume_orchestrator_events() -> list[tuple[str, dict]]:
    """异步拉出当前 deque 中的所有事件（不阻塞）。"""
    events: list[tuple[str, dict]] = []
    while True:
        try:
            events.append(_orchestrator_event_queue.popleft())
        except IndexError:
            break
    return events


def peek_orchestrator_events() -> list[tuple[str, dict]]:
    """只读窥视（测试用；不消费）。"""
    return list(_orchestrator_event_queue)


def flush_orchestrator_events() -> None:
    """清空事件队列（仅测试用）。"""
    _orchestrator_event_queue.clear()
