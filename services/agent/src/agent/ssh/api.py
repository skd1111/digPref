"""Phase 2B V0 · FastAPI 5 端点 —— SSH 会话管理 + 命令执行 + SFTP。

端点：
  - POST /ssh/connect      —— 创建会话 + 连接
  - POST /ssh/disconnect/{session_id} —— 断开指定 session
  - POST /ssh/exec         —— 在已有 session 上执行命令
  - POST /ssh/sftp/ls      —— 列出 SFTP 目录
  - POST /ssh/sftp/get     —— 下载远程文件到本地
  - GET  /ssh/sessions     —— 列出活动会话（in-memory）+ 数据库历史
  - GET  /ssh/sessions/{session_id}/commands —— 某 session 的命令历史
  - GET  /ssh/stats        —— 统计

V0 PoC 范围：仅 password 认证 + 单命令执行（echo 模式）+ SFTP 列目录 + 下载。
V1 接力：PTY 交互 + publickey + 端口转发 + 跳板机。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.ssh.events import (
    EVT_SSH_COMMAND_DONE,
    EVT_SSH_CONNECTED,
    EVT_SSH_DISCONNECTED,
    EVT_SSH_ERROR,
    emit_event_sync,
)
from agent.ssh.models import (
    AuthMethod,
    PtyMode,
    SshAuthError,
    SshCommandError,
    SshConnectionError,
    SshError,
    SshPathSecurityError,
)
from agent.ssh.session_manager import get_default_manager
from agent.ssh.storage import get_default_storage

router = APIRouter(prefix="/ssh", tags=["ssh"])


# ---- Pydantic schemas ------------------------------------------------------


class ConnectRequest(BaseModel):
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str
    password: str | None = None
    auth_method: AuthMethod = AuthMethod.PASSWORD
    pty_mode: PtyMode = PtyMode.ECHO
    connect_timeout: float = Field(default=10.0, ge=1.0, le=60.0)


class DisconnectResponse(BaseModel):
    session_id: str
    disconnected: bool


class ExecRequest(BaseModel):
    session_id: str
    command: str = Field(min_length=1, max_length=8192)
    timeout_sec: float = Field(default=30.0, ge=1.0, le=300.0)


class SftpLsRequest(BaseModel):
    session_id: str
    path: str = "/"


class SftpGetRequest(BaseModel):
    session_id: str
    remote_path: str
    local_path: str


class SessionResponse(BaseModel):
    session_id: str
    host: str
    port: int
    username: str
    auth_method: str
    status: str
    pty_mode: str
    created_at: str
    last_used: str | None = None


# ---- 6 端点 --------------------------------------------------------------


@router.post("/connect", response_model=SessionResponse)
async def connect(req: ConnectRequest) -> SessionResponse:
    """创建会话 + 连接（V0 password 认证）。"""
    mgr = get_default_manager()
    try:
        meta = await mgr.connect(
            host=req.host,
            port=req.port,
            username=req.username,
            password=req.password,
            auth_method=req.auth_method,
            pty_mode=req.pty_mode,
            connect_timeout=req.connect_timeout,
        )
    except SshAuthError as exc:
        emit_event_sync(
            EVT_SSH_ERROR,
            {
                "kind": EVT_SSH_ERROR,
                "host": req.host,
                "port": req.port,
                "username": req.username,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=401, detail=f"auth failed: {exc}")
    except SshConnectionError as exc:
        emit_event_sync(
            EVT_SSH_ERROR,
            {
                "kind": EVT_SSH_ERROR,
                "host": req.host,
                "port": req.port,
                "username": req.username,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=502, detail=f"connection failed: {exc}")
    except SshPathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 写 storage（best-effort）
    try:
        storage = get_default_storage()
        await storage.insert_session(
            session_id=meta.session_id,
            host=meta.host,
            port=meta.port,
            username=meta.username,
            auth_method=meta.auth_method.value,
            status=meta.status.value,
            pty_mode=meta.pty_mode.value,
            meta={"pty_mode": meta.pty_mode.value},
        )
    except Exception:
        pass

    emit_event_sync(
        EVT_SSH_CONNECTED,
        {
            "kind": EVT_SSH_CONNECTED,
            "session_id": meta.session_id,
            "host": meta.host,
            "port": meta.port,
            "username": meta.username,
        },
    )
    return SessionResponse(
        session_id=meta.session_id,
        host=meta.host,
        port=meta.port,
        username=meta.username,
        auth_method=meta.auth_method.value,
        status=meta.status.value,
        pty_mode=meta.pty_mode.value,
        created_at=meta.created_at,
        last_used=meta.last_used,
    )


@router.post("/disconnect/{session_id}", response_model=DisconnectResponse)
async def disconnect(session_id: str) -> DisconnectResponse:
    """断开指定 session。"""
    mgr = get_default_manager()
    ok = await mgr.disconnect(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")

    try:
        storage = get_default_storage()
        await storage.update_session_status(session_id, "disconnected")
    except Exception:
        pass

    emit_event_sync(
        EVT_SSH_DISCONNECTED,
        {
            "kind": EVT_SSH_DISCONNECTED,
            "session_id": session_id,
        },
    )
    return DisconnectResponse(session_id=session_id, disconnected=True)


@router.post("/exec")
async def exec_command(req: ExecRequest) -> dict[str, Any]:
    """在已有 session 上执行命令。"""
    mgr = get_default_manager()
    client = mgr.get_client(req.session_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"session not found: {req.session_id}")

    try:
        result = await client.exec_command(req.command, timeout_sec=req.timeout_sec)
    except SshCommandError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except SshPathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 写 storage + touch
    try:
        storage = get_default_storage()
        await storage.insert_command(
            session_id=req.session_id,
            command=result.command,
            exit_code=result.exit_code,
            elapsed_ms=result.elapsed_ms,
            ok=result.ok,
            error=result.error,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        await storage.touch_session(req.session_id)
        await mgr.touch(req.session_id)
    except Exception:
        pass

    emit_event_sync(
        EVT_SSH_COMMAND_DONE,
        {
            "kind": EVT_SSH_COMMAND_DONE,
            "session_id": req.session_id,
            "command": result.command,
            "ok": result.ok,
            "exit_code": result.exit_code,
            "elapsed_ms": result.elapsed_ms,
        },
    )
    return {
        "session_id": req.session_id,
        "command": result.command,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_ms": result.elapsed_ms,
        "ok": result.ok,
        "error": result.error,
    }


@router.post("/sftp/ls")
async def sftp_ls(req: SftpLsRequest) -> dict[str, Any]:
    """列出 SFTP 目录。"""
    mgr = get_default_manager()
    client = mgr.get_client(req.session_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"session not found: {req.session_id}")

    try:
        entries = await client.sftp_ls(req.path)
    except SshPathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SshError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "session_id": req.session_id,
        "path": req.path,
        "entries": [
            {
                "name": e.name,
                "path": e.path,
                "is_dir": e.is_dir,
                "size": e.size,
                "mtime": e.mtime,
                "permissions": e.permissions,
            }
            for e in entries
        ],
        "count": len(entries),
    }


@router.post("/sftp/get")
async def sftp_get(req: SftpGetRequest) -> dict[str, Any]:
    """下载远程文件到本地。"""
    mgr = get_default_manager()
    client = mgr.get_client(req.session_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"session not found: {req.session_id}")

    try:
        bytes_downloaded = await client.sftp_get(req.remote_path, req.local_path)
    except SshPathSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SshError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "session_id": req.session_id,
        "remote_path": req.remote_path,
        "local_path": req.local_path,
        "bytes_downloaded": bytes_downloaded,
    }


@router.get("/sessions")
async def list_sessions() -> dict[str, Any]:
    """列活动会话（in-memory）+ 数据库历史（最近 50）。"""
    mgr = get_default_manager()
    storage = get_default_storage()
    active = mgr.list_sessions()
    history = await storage.list_sessions(limit=50)
    return {
        "active_count": len(active),
        "active": [
            {
                "session_id": s.session_id,
                "host": s.host,
                "port": s.port,
                "username": s.username,
                "status": s.status.value,
                "created_at": s.created_at,
                "last_used": s.last_used,
            }
            for s in active
        ],
        "history": history,
    }


@router.get("/sessions/{session_id}/commands")
async def list_commands(session_id: str, limit: int = 50) -> dict[str, Any]:
    """某 session 的命令历史。"""
    storage = get_default_storage()
    cmds = await storage.list_commands(session_id, limit=min(limit, 500))
    return {"session_id": session_id, "count": len(cmds), "commands": cmds}


@router.get("/stats")
async def stats() -> dict[str, Any]:
    storage = get_default_storage()
    return await storage.get_stats()
