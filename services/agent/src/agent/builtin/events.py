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

# 执行过程可视化（Claude Code 式）细粒度事件（与 graph/stream.py::_CHANNEL_BY_KIND 一致）
EVT_TOOL_PROGRESS = "tool_progress"
EVT_SHELL_CHUNK = "shell_chunk"
EVT_FILE_WRITE_PREVIEW = "file_write_preview"

# 回答逐字流式（2026-09-03）：responder 终答路径的 token 增量，复用本队列
# 的 deque + stream.py 0.4s 轮询 drain，零新增管道（与 shell_chunk 同节奏）。
EVT_ANSWER_DELTA = "answer_delta"


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
    run_id: str | None = None,
) -> None:
    """Emit builtin_tool_started 事件。

    Args:
        tool_name: 工具名（不含 builtin_ 前缀）。
        args: 工具参数（已 scrub 过路径 basename）。
        risk_level: 风险等级（read / low / medium / high / critical）。
        needs_hitl: 是否触发 HITL。
        call_id: 调度批次 ID（UUID4 hex）。
        run_id: 所属 run（多会话并发时前端按此路由到对应页签）。
    """
    await emit_builtin_event(
        EVT_BUILTIN_TOOL_STARTED,
        {
            "kind": EVT_BUILTIN_TOOL_STARTED,
            "tool_name": tool_name,
            "args_keys": sorted(args.keys()),
            "risk_level": risk_level,
            "needs_hitl": needs_hitl,
            "call_id": call_id,
            **({"runId": run_id} if run_id else {}),
        },
    )


def emit_tool_started_sync(
    *,
    tool_name: str,
    args: dict[str, Any],
    risk_level: str,
    needs_hitl: bool,
    call_id: str,
    run_id: str | None = None,
) -> None:
    """同步版 emit_tool_started（dispatcher 在 to_thread 内调用）。"""
    emit_builtin_event_sync(
        EVT_BUILTIN_TOOL_STARTED,
        {
            "kind": EVT_BUILTIN_TOOL_STARTED,
            "tool_name": tool_name,
            "args_keys": sorted(args.keys()),
            "risk_level": risk_level,
            "needs_hitl": needs_hitl,
            "call_id": call_id,
            **({"runId": run_id} if run_id else {}),
        },
    )


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
    run_id: str | None = None,
) -> None:
    """Emit builtin_tool_done 事件。"""
    await emit_builtin_event(
        EVT_BUILTIN_TOOL_DONE,
        {
            "kind": EVT_BUILTIN_TOOL_DONE,
            "tool_name": tool_name,
            "call_id": call_id,
            "ok": ok,
            "error": error,
            "elapsed_ms": elapsed_ms,
            "risk_level": risk_level,
            "content_size": content_size,
            "result_meta": result_meta or {},
            **({"runId": run_id} if run_id else {}),
        },
    )


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
    run_id: str | None = None,
) -> None:
    """同步版 emit_tool_done。"""
    emit_builtin_event_sync(
        EVT_BUILTIN_TOOL_DONE,
        {
            "kind": EVT_BUILTIN_TOOL_DONE,
            "tool_name": tool_name,
            "call_id": call_id,
            "ok": ok,
            "error": error,
            "elapsed_ms": elapsed_ms,
            "risk_level": risk_level,
            "content_size": content_size,
            "result_meta": result_meta or {},
            **({"runId": run_id} if run_id else {}),
        },
    )


async def emit_tool_denied(
    *,
    tool_name: str,
    call_id: str,
    approval_id: str,
    reason: str,
) -> None:
    """Emit builtin_tool_denied 事件（HITL 拒绝后）。"""
    await emit_builtin_event(
        EVT_BUILTIN_TOOL_DENIED,
        {
            "kind": EVT_BUILTIN_TOOL_DENIED,
            "tool_name": tool_name,
            "call_id": call_id,
            "approval_id": approval_id,
            "reason": reason,
        },
    )


# ---- 执行过程可视化（Claude Code 式）细粒度事件工厂 -------------------------
#
# 三类事件与 tool_call/tool_result 共享 call_id（BUGFIX #164 配对键），
# 前端按 call_id 归并到对应工具卡：
#   - tool_progress：长耗时工具的阶段文案（搜索/编译/批处理）
#   - shell_chunk：shell 执行期间的流式输出片段，结束帧带 exit_code
#   - file_write_preview：写类工具落盘前的 unified diff 预览（配审批）


async def emit_tool_progress(
    *,
    call_id: str,
    message: str,
    tool_name: str | None = None,
    percent: float | None = None,
    run_id: str | None = None,
) -> None:
    """Emit tool_progress 事件。"""
    await emit_builtin_event(
        EVT_TOOL_PROGRESS,
        {
            "kind": EVT_TOOL_PROGRESS,
            "call_id": call_id,
            "tool_name": tool_name,
            "message": message,
            "percent": percent,
            **({"runId": run_id} if run_id else {}),
        },
    )


def emit_tool_progress_sync(
    *,
    call_id: str,
    message: str,
    tool_name: str | None = None,
    percent: float | None = None,
    run_id: str | None = None,
) -> None:
    """同步版 emit_tool_progress（to_thread 内调用）。"""
    emit_builtin_event_sync(
        EVT_TOOL_PROGRESS,
        {
            "kind": EVT_TOOL_PROGRESS,
            "call_id": call_id,
            "tool_name": tool_name,
            "message": message,
            "percent": percent,
            **({"runId": run_id} if run_id else {}),
        },
    )


async def emit_shell_chunk(
    *,
    call_id: str,
    chunk: str,
    stream: str = "stdout",
    exit_code: int | None = None,
    run_id: str | None = None,
) -> None:
    """Emit shell_chunk 事件（stream=stdout/stderr；结束帧带 exit_code）。"""
    await emit_builtin_event(
        EVT_SHELL_CHUNK,
        {
            "kind": EVT_SHELL_CHUNK,
            "call_id": call_id,
            "stream": stream,
            "chunk": chunk,
            "exit_code": exit_code,
            **({"runId": run_id} if run_id else {}),
        },
    )


def emit_shell_chunk_sync(
    *,
    call_id: str,
    chunk: str,
    stream: str = "stdout",
    exit_code: int | None = None,
    run_id: str | None = None,
) -> None:
    """同步版 emit_shell_chunk（to_thread 内调用）。"""
    emit_builtin_event_sync(
        EVT_SHELL_CHUNK,
        {
            "kind": EVT_SHELL_CHUNK,
            "call_id": call_id,
            "stream": stream,
            "chunk": chunk,
            "exit_code": exit_code,
            **({"runId": run_id} if run_id else {}),
        },
    )


async def emit_answer_delta(
    *,
    run_id: str,
    msg_id: str,
    delta: str,
) -> None:
    """Emit answer_delta 事件（回答逐字流式，2026-09-03）。

    msg_id 由 responder 生成并携带在每条 delta 上；终答 message 事件复用同一
    id（stream.py 种子化 final_answer_msg_id），前端按 id 原地覆盖收敛。
    """
    await emit_builtin_event(
        EVT_ANSWER_DELTA,
        {
            "kind": EVT_ANSWER_DELTA,
            "runId": run_id,
            "msgId": msg_id,
            "delta": delta,
        },
    )


async def emit_file_write_preview(
    *,
    call_id: str,
    path: str,
    diff: str,
    risk_level: str | None = None,
    run_id: str | None = None,
) -> None:
    """Emit file_write_preview 事件（写前 unified diff 预览）。"""
    await emit_builtin_event(
        EVT_FILE_WRITE_PREVIEW,
        {
            "kind": EVT_FILE_WRITE_PREVIEW,
            "call_id": call_id,
            "path": path,
            "diff": diff,
            "risk_level": risk_level,
            **({"runId": run_id} if run_id else {}),
        },
    )


def emit_file_write_preview_sync(
    *,
    call_id: str,
    path: str,
    diff: str,
    risk_level: str | None = None,
    run_id: str | None = None,
) -> None:
    """同步版 emit_file_write_preview。"""
    emit_builtin_event_sync(
        EVT_FILE_WRITE_PREVIEW,
        {
            "kind": EVT_FILE_WRITE_PREVIEW,
            "call_id": call_id,
            "path": path,
            "diff": diff,
            "risk_level": risk_level,
            **({"runId": run_id} if run_id else {}),
        },
    )
