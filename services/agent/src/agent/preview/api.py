"""Phase 15 V0 · FastAPI 预览引擎路由（/preview/*）。

端点：
  - POST /preview/start              启动预览（自动框架检测 + 端口避让）
  - POST /preview/stop/{session_id}  停止预览
  - GET  /preview/sessions           列出活跃会话
  - GET  /preview/info/{session_id}  会话详情
  - POST /preview/reload/{session_id} 强制刷新
  - POST /preview/install/{session_id} 手动触发依赖安装（带进度回调）
  - GET  /preview/stream/{session_id} SSE —— HMR 状态 / 编译错误 / 安装进度
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from agent.preview import events as preview_events
from agent.preview.install_manager import ensure_dependencies
from agent.preview.models import (
    PreviewSession,
    StartPreviewRequest,
)
from agent.preview.session_manager import (
    PreviewError,
    SessionManager,
    get_default_manager,
)

router = APIRouter(prefix="/preview", tags=["preview"])


def _manager(request: Request) -> SessionManager:
    """取全局 SessionManager（测试可重置）。"""
    return get_default_manager()


@router.post("/start", response_model=PreviewSession)
async def start_preview(req: StartPreviewRequest, request: Request) -> PreviewSession:
    try:
        return await _manager(request).start(req)
    except PreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stop/{session_id}", response_model=PreviewSession)
async def stop_preview(session_id: str, request: Request) -> PreviewSession:
    try:
        return await _manager(request).stop(session_id)
    except PreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sessions", response_model=list[PreviewSession])
async def list_sessions(
    request: Request,
    active_only: bool = Query(False),
) -> list[PreviewSession]:
    mgr = _manager(request)
    sessions = mgr.list_active() if active_only else mgr.list_all()
    return sessions


@router.get("/info/{session_id}", response_model=PreviewSession)
async def get_session_info(session_id: str, request: Request) -> PreviewSession:
    session = _manager(request).get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    return session


@router.post("/reload/{session_id}", response_model=PreviewSession)
async def reload_preview(session_id: str, request: Request) -> PreviewSession:
    try:
        return await _manager(request).reload(session_id)
    except PreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/install/{session_id}")
async def trigger_install(session_id: str, request: Request) -> dict[str, Any]:
    """手动触发依赖安装（后台执行，进度走 SSE）。"""
    mgr = _manager(request)
    session = mgr.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    await mgr.set_install_progress(session_id, 1)

    async def _run() -> None:
        await ensure_dependencies(session.project_path, session_id)
        await mgr.set_install_progress(session_id, 100)

    asyncio.create_task(_run())
    return {"session_id": session_id, "install_started": True}


@router.get("/stream/{session_id}")
async def preview_stream(session_id: str, request: Request) -> StreamingResponse:
    """SSE 长连接：推 HMR 状态 / 编译错误 / 安装进度事件。"""
    mgr = _manager(request)
    session = mgr.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")

    queue = await preview_events.subscribe(session_id)

    async def event_gen() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=15.0)
                    mgr.touch(session_id)
                    yield _sse_format(envelope)
                except asyncio.TimeoutError:
                    # 心跳保活 + 顺便检查会话是否已被停止
                    cur = mgr.get(session_id)
                    if cur is None or cur.status.value in ("stopped", "errored"):
                        break
                    yield ": heartbeat\n\n"
        finally:
            await preview_events.unsubscribe(session_id, queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse_format(envelope: dict[str, object]) -> str:
    event = envelope.get("event", "message")
    data = envelope.get("data")
    if not isinstance(data, str):
        data = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"
