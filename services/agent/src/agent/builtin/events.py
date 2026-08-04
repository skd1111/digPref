"""Phase 1B V1 · Builtin 工具事件 emit 机制。

仿照 agent.skills.events 的进程内 deque + asyncio 锁设计：
  - ToolDispatcher 在执行工具前后 emit 3 类事件：
    * builtin_tool_started —— dispatcher 进入前（含 tool_name + risk_level + needs_hitl）
    * builtin_tool_done —— ToolResult 出来后（含 ok + content_size + elapsed_ms + result meta）
    * builtin_tool_denied —— HITL 拒绝后（含 reason + approver + approval_id）
  - stream.py::_drain_builtin_events() 在流循环 + finally 各调一次

V1 不跨进程（EAIDE 是单 Agent 进程，跨进程用 SSE 即可）。
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any


# 进程内事件队列（无界，由 stream.py 流式消费）
_builtin_events: deque[tuple[str, dict]] = deque()
_builtin_lock = asyncio.Lock()


# ---- 公开事件常量（与 graph/stream.py::_CHANNEL_BY_KIND 严格一致）--------

EVT_BUILTIN_TOOL_STARTED = "builtin_tool_started"
EVT_BUILTIN_TOOL_DONE = "builtin_tool_done"
EVT_BUILTIN_TOOL_DENIED = "builtin_tool_denied"


async def emit_builtin_event(kind: str, payload: dict) -> None:
    """Emit 一个 builtin 事件到进程内队列。

    Args:
        kind: 事件类型（builtin_tool_started / done / denied）。
        payload: 事件数据（dict）。

    Note:
        best-effort：调用方不阻塞，错误吞掉。SSE 推送失败不应影响工具执行。
    """
    async with _builtin_lock:
        _builtin_events.append((kind, payload))


def emit_builtin_event_sync(kind: str, payload: dict) -> None:
    """同步版 emit（供 ToolDispatcher 在 asyncio.to_thread 内调用）。

    内部用 put_nowait 风格：deque.append 永不阻塞；锁用 asyncio.Lock 但
    此函数不 await 也不持锁（callers 不需要强一致性 —— SSE 顺序允许轻微漂移）。
    """
    _builtin_events.append((kind, payload))


async def consume_builtin_events(timeout_s: float = 0.0) -> list[tuple[str, dict]]:
    """消费当前缓冲区所有 builtin 事件（FIFO）。

    Args:
        timeout_s: 等待新事件的秒数（0 = 不等待，立即返回当前 buffered）。

    Returns:
        [(kind, payload), ...] 已 emit 的事件列表。

    Note:
        V1 实现：timeout_s 保留接口（与 skills.events / biznav.events 一致），
        但 V1 不做等待循环（事件 emit 时机已经覆盖 stream.py 流循环 + finally）。
        如未来需要做"实时推"，可加 asyncio.wait_for。
    """
    events: list[tuple[str, dict]] = []
    async with _builtin_lock:
        while _builtin_events:
            events.append(_builtin_events.popleft())
    _ = timeout_s  # 接口兼容
    return events


async def flush_builtin_events() -> int:
    """清空当前缓冲区（测试 hook）。返回丢弃的事件数。"""
    async with _builtin_lock:
        count = len(_builtin_events)
        _builtin_events.clear()
    return count


# ---- 工厂辅助（dispatcher 调用更顺手）---------------------------------------

async def emit_tool_started(
    *,
    tool_name: str,
    args: dict[str, Any],
    risk_level: str,
    needs_hitl: bool,
    call_id: str,
) -> None:
    """Emit builtin_tool_started 事件。

    Args:
        tool_name: 工具名（不含 builtin_ 前缀）。
        args: 工具参数（已 scrub 过路径 basename）。
        risk_level: 风险等级（read / low / medium / high / critical）。
        needs_hitl: 是否触发 HITL。
        call_id: 调度批次 ID（UUID4 hex）。
    """
    await emit_builtin_event(EVT_BUILTIN_TOOL_STARTED, {
        "kind": EVT_BUILTIN_TOOL_STARTED,
        "tool_name": tool_name,
        "args_keys": sorted(args.keys()),
        "risk_level": risk_level,
        "needs_hitl": needs_hitl,
        "call_id": call_id,
    })


def emit_tool_started_sync(
    *,
    tool_name: str,
    args: dict[str, Any],
    risk_level: str,
    needs_hitl: bool,
    call_id: str,
) -> None:
    """同步版 emit_tool_started（dispatcher 在 to_thread 内调用）。"""
    emit_builtin_event_sync(EVT_BUILTIN_TOOL_STARTED, {
        "kind": EVT_BUILTIN_TOOL_STARTED,
        "tool_name": tool_name,
        "args_keys": sorted(args.keys()),
        "risk_level": risk_level,
        "needs_hitl": needs_hitl,
        "call_id": call_id,
    })


async def emit_tool_done(
    *,
    tool_name: str,
    call_id: str,
    ok: bool,
    error: str | None,
    elapsed_ms: int,
    risk_level: str,
    content_size: int,
    result_meta: dict | None = None,
) -> None:
    """Emit builtin_tool_done 事件。"""
    await emit_builtin_event(EVT_BUILTIN_TOOL_DONE, {
        "kind": EVT_BUILTIN_TOOL_DONE,
        "tool_name": tool_name,
        "call_id": call_id,
        "ok": ok,
        "error": error,
        "elapsed_ms": elapsed_ms,
        "risk_level": risk_level,
        "content_size": content_size,
        "result_meta": result_meta or {},
    })


def emit_tool_done_sync(
    *,
    tool_name: str,
    call_id: str,
    ok: bool,
    error: str | None,
    elapsed_ms: int,
    risk_level: str,
    content_size: int,
    result_meta: dict | None = None,
) -> None:
    """同步版 emit_tool_done。"""
    emit_builtin_event_sync(EVT_BUILTIN_TOOL_DONE, {
        "kind": EVT_BUILTIN_TOOL_DONE,
        "tool_name": tool_name,
        "call_id": call_id,
        "ok": ok,
        "error": error,
        "elapsed_ms": elapsed_ms,
        "risk_level": risk_level,
        "content_size": content_size,
        "result_meta": result_meta or {},
    })


async def emit_tool_denied(
    *,
    tool_name: str,
    call_id: str,
    approval_id: str,
    reason: str,
) -> None:
    """Emit builtin_tool_denied 事件（HITL 拒绝后）。"""
    await emit_builtin_event(EVT_BUILTIN_TOOL_DENIED, {
        "kind": EVT_BUILTIN_TOOL_DENIED,
        "tool_name": tool_name,
        "call_id": call_id,
        "approval_id": approval_id,
        "reason": reason,
    })