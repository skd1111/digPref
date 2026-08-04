"""Phase 15 V0 · 预览引擎事件 emit 机制。

仿照 builtin.events / image_processing.events 设计：进程内 deque + asyncio 锁。
stream.py::_drain_preview_events 拉取转 SSE 主通道；api.py 的
`/preview/stream/{session_id}` 另建每会话订阅队列（broadcast）。

事件类型（与 graph/stream.py::_CHANNEL_BY_KIND 严格一致）：
  - preview_hmr_connected     —— HMR WebSocket 连接成功
  - preview_hmr_disconnected  —— HMR WebSocket 断开 / 重连中
  - preview_build_error       —— Vite 编译错误
  - preview_install_progress  —— node_modules 后台安装进度（仅预览会话流）
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress

_preview_events: deque[tuple[str, dict[str, object]]] = deque()
_preview_lock = asyncio.Lock()

# 每会话订阅队列（broadcast）：{session_id: set[asyncio.Queue]}
_subscribers: dict[str, set[asyncio.Queue[dict[str, object]]]] = {}
_sub_lock = asyncio.Lock()


# ---- 公开事件常量（三处同步：stream.py / sse_bridge.rs / events.ts）-----

EVT_PREVIEW_HMR_CONNECTED = "preview_hmr_connected"
EVT_PREVIEW_HMR_DISCONNECTED = "preview_hmr_disconnected"
EVT_PREVIEW_BUILD_ERROR = "preview_build_error"
EVT_PREVIEW_INSTALL_PROGRESS = "preview_install_progress"


async def emit_event(kind: str, payload: dict[str, object]) -> None:
    async with _preview_lock:
        _preview_events.append((kind, payload))
    await _broadcast(kind, payload)


def emit_event_sync(kind: str, payload: dict[str, object]) -> None:
    """同步版 emit（线程 / to_thread 内调用）。"""
    _preview_events.append((kind, payload))
    # 广播不能在这里 await；为线程内调用提供一个尽力而为的同步广播
    session_id = str(payload.get("session_id") or "")
    if session_id:
        for q in list(_subscribers.get(session_id, set())):
            with suppress(asyncio.QueueFull):
                q.put_nowait({"event": kind, "data": payload})


async def consume_events(timeout_s: float = 0.0) -> list[tuple[str, dict[str, object]]]:
    """拉取全部 buffered 事件（stream.py 主通道消费）。"""
    events: list[tuple[str, dict[str, object]]] = []
    async with _preview_lock:
        while _preview_events:
            events.append(_preview_events.popleft())
    return events


async def flush_events() -> int:
    async with _preview_lock:
        count = len(_preview_events)
        _preview_events.clear()
    return count


# ---- 每会话 SSE 订阅（/preview/stream/{session_id}）-----------------------


async def subscribe(session_id: str, maxsize: int = 256) -> asyncio.Queue[dict[str, object]]:
    """为会话创建一个订阅队列（SSE 端点长连接用）。"""
    q: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=maxsize)
    async with _sub_lock:
        _subscribers.setdefault(session_id, set()).add(q)
    return q


async def unsubscribe(session_id: str, q: asyncio.Queue[dict[str, object]]) -> None:
    async with _sub_lock:
        subs = _subscribers.get(session_id)
        if subs is not None:
            subs.discard(q)
            if not subs:
                _subscribers.pop(session_id, None)


async def _broadcast(kind: str, payload: dict[str, object]) -> None:
    """把事件推给该会话的所有 SSE 订阅者。"""
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return
    async with _sub_lock:
        subs = list(_subscribers.get(session_id, set()))
    if not subs:
        return
    envelope: dict[str, object] = {"event": kind, "data": payload}
    for q in subs:
        with suppress(asyncio.QueueFull):
            q.put_nowait(envelope)
