"""biznav.events —— Phase 2G V1.3 业务功能点 SSE 事件 emit 机制。

设计（参照 agent.llm.metrics 的 _router_event_queue 风格）：
    - 进程级 deque：biznav/hot_reload.py / incremental.py /
      api.py 后台 extract 任务等模块 emit_biznav_event() 写入；
    - graph/stream.py 的 `consume_biznav_events()` 在流循环里异步拉出来
      推到 SSE 前端（`agent://biznav_yaml_reloaded` / `agent://biznav_feature_affected` /
      `agent://biznav_extraction_done`）。
    - deque 上限 1000 条防 memory leak；旧的会被丢弃（前端不会无限累积）
    - CPython GIL 保证单个 append/popleft 原子；asyncio 协作式调度 + 单事件循环
      下 emit/consume 无数据竞争（不同于多线程）

CLAUDE.md §4 SSE 三处同步：
    - Python emit (本文件) → graph/stream.py 转发 → Rust sse_bridge.rs::channel
      → TS ipc/events.ts::EVT
    - 通道名必须严格一致：`biznav_yaml_reloaded` / `biznav_feature_affected` /
      `biznav_extraction_done`
"""
from __future__ import annotations

from collections import deque
from typing import Any

# 进程级 deque（asyncio 单线程安全：GIL 保证 append/popleft 原子性）
_biznav_event_queue: deque[tuple[str, dict]] = deque(maxlen=1000)


# ---- 3 个事件通道名常量（与 sse_bridge.rs::channel + events.ts::EVT 同步）----
EVT_YAML_RELOADED: str = "biznav_yaml_reloaded"
EVT_FEATURE_AFFECTED: str = "biznav_feature_affected"
EVT_EXTRACTION_DONE: str = "biznav_extraction_done"


def emit_biznav_event(kind: str, payload: dict[str, Any]) -> None:
    """把 biznav 事件写入进程内 deque。

    `kind` 必须是 `EVT_YAML_RELOADED` / `EVT_FEATURE_AFFECTED` /
    `EVT_EXTRACTION_DONE` 之一（未来扩展时再扩 frozenset）。

    `payload` 会被 `graph/stream.py` 直接 json.dumps 后通过 SSE 推到前端。
    """
    _biznav_event_queue.append((kind, payload))


async def consume_biznav_events(timeout_s: float = 0.0) -> list[tuple[str, dict]]:
    """异步拉出当前 deque 中的所有事件（不阻塞）。

    `graph/stream.py` 流循环 + finally 各调一次，确保 buffered 事件全部推完。

    测试用：调 `flush_biznav_events()` 清空队列。
    """
    events: list[tuple[str, dict]] = []
    while True:
        try:
            kind, payload = _biznav_event_queue.popleft()
            events.append((kind, payload))
        except IndexError:
            break
    return events


def flush_biznav_events() -> None:
    """清空事件队列（仅测试用）。"""
    _biznav_event_queue.clear()