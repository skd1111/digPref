"""Phase 16 · /trace FastAPI 路由 —— 思维链查询接口。

端点：
    GET /trace/sessions                      —— 最近会话列表（前端启动时自动加载用）
    GET /trace/session/{session_id}          —— 会话全部思维链步骤（时间线）
    GET /trace/step/{step_id}                —— 单步详情
    GET /trace/file-diff/{step_id}/{idx}     —— 某步第 idx 个文件操作的完整 diff
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from agent.trace import storage

router = APIRouter(prefix="/trace", tags=["trace"])


@router.get("/sessions")
async def trace_sessions(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """返回最近有思维链记录的会话列表（按最近活动倒序）。"""
    sessions = await storage.recent_sessions(limit=limit)
    return {"count": len(sessions), "sessions": sessions}


@router.get("/session/{session_id}")
async def trace_session(
    session_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """返回会话的思维链时间线（step_index 升序）。"""
    steps = await storage.list_steps(session_id, limit=limit, offset=offset)
    return {
        "session_id": session_id,
        "count": len(steps),
        "offset": offset,
        "steps": [s.to_dict() for s in steps],
    }


@router.get("/step/{step_id}")
async def trace_step(step_id: str) -> dict:
    """返回单条思维链步骤详情。"""
    step = await storage.get_step(step_id)
    if step is None:
        raise HTTPException(status_code=404, detail=f"step not found: {step_id}")
    return step.to_dict()


@router.get("/file-diff/{step_id}/{file_index}")
async def trace_file_diff(step_id: str, file_index: int) -> dict:
    """返回某步第 file_index 个文件操作的完整 diff（hover 懒加载用）。"""
    step = await storage.get_step(step_id)
    if step is None:
        raise HTTPException(status_code=404, detail=f"step not found: {step_id}")
    ops = step.file_operations
    if file_index < 0 or file_index >= len(ops):
        raise HTTPException(
            status_code=404,
            detail=f"file_index out of range: {file_index} (共 {len(ops)} 个文件操作)",
        )
    op = ops[file_index]
    return {
        "step_id": step_id,
        "file_index": file_index,
        "type": op.type,
        "path": op.path,
        "diff": op.diff or "",
        "preview": op.preview or "",
        "lines_added": op.lines_added,
        "lines_removed": op.lines_removed,
        "ok": op.ok,
        "error": op.error,
    }
