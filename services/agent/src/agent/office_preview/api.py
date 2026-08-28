"""Office 文件预览 —— FastAPI 路由（OfficeCLI 渲染引擎，2026-08-25）。

把 docx / xlsx / pptx 用 OfficeCLI 内置渲染引擎转成 HTML（资源内联）或
逐页 PNG，供前端预览面板 / 独立窗口展示。与 Phase 15 前端实时预览同哲学：
    - 只读操作：不落审计写记录、不触发 HITL
    - 路径沙箱：仅允许 docx/xlsx/pptx 且过 validate_path
    - 优雅降级：OfficeCLI 缺失 / 渲染失败均返结构化错误而非 500
"""

from __future__ import annotations

import base64
import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from agent.builtin.officecli_runtime import (
    OFFICE_SUFFIXES,
    resolve_officecli_exe,
    run_officecli,
)
from agent.builtin.path_sandbox import validate_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/office/preview", tags=["office-preview"])

# 会话上限（渲染产物驻留内存 / 临时目录；超限按创建顺序淘汰最旧）
_MAX_SESSIONS = 16


@dataclass
class _PreviewSession:
    session_id: str
    source: str
    mode: str  # 'html' | 'screenshot'
    artifact: Path  # 渲染产物文件（.html / .png）
    workdir: Path = field(repr=False)  # 临时目录（停止时整目录删除）


_sessions: dict[str, _PreviewSession] = {}


class PreviewRequest(BaseModel):
    path: str = Field(description="docx / xlsx / pptx 文件绝对路径")
    mode: str = Field(default="html", description="html=内嵌渲染页；screenshot=逐页 PNG")
    page: int | None = Field(default=None, ge=1, description="screenshot 模式的页码（缺省首页）")


class StopRequest(BaseModel):
    session_id: str


def _evict_if_needed() -> None:
    """超过会话上限时淘汰最旧会话（best-effort 清理临时目录）。"""
    while len(_sessions) >= _MAX_SESSIONS:
        oldest = next(iter(_sessions.values()))
        _sessions.pop(oldest.session_id, None)
        shutil.rmtree(oldest.workdir, ignore_errors=True)


def _check_office_source(path: str) -> Path:
    try:
        p = validate_path(path, must_exist=True)
    except Exception as exc:
        raise HTTPException(400, f"invalid_path: {exc}") from exc
    if not p.is_file() or p.suffix.lower() not in OFFICE_SUFFIXES:
        raise HTTPException(400, f"not_an_office_file: 仅支持 docx / xlsx / pptx: {p}")
    return p


@router.post("")
def create_preview(body: PreviewRequest) -> dict[str, Any]:
    """渲染 Office 文件为预览产物（html → 会话 URL；screenshot → base64 PNG）。"""
    if body.mode not in ("html", "screenshot"):
        raise HTTPException(400, "mode 必须是 html / screenshot")
    src = _check_office_source(body.path)
    if not resolve_officecli_exe():
        raise HTTPException(
            503,
            "officecli_not_installed: 请运行 infra/scripts/fetch-officecli.ps1 "
            "或用 EAIDE_BUILTIN_OFFICECLI_EXECUTABLE 指定二进制",
        )

    workdir = Path(tempfile.mkdtemp(prefix="eaide-office-preview-"))
    session_id = uuid.uuid4().hex[:12]
    try:
        if body.mode == "screenshot":
            out_png = workdir / f"{src.stem}.png"
            args = ["view", str(src), "screenshot", "-o", str(out_png)]
            if body.page:
                args.extend(["--page", str(body.page)])
            outcome = run_officecli(args, as_json=False)
            if not outcome.ok or not out_png.is_file():
                shutil.rmtree(workdir, ignore_errors=True)
                raise HTTPException(
                    422,
                    f"render_failed: {outcome.suggestion or outcome.message or outcome.error}",
                )
            payload = base64.b64encode(out_png.read_bytes()).decode("ascii")
            _evict_if_needed()
            _sessions[session_id] = _PreviewSession(
                session_id=session_id,
                source=str(src),
                mode="screenshot",
                artifact=out_png,
                workdir=workdir,
            )
            return {
                "ok": True,
                "session_id": session_id,
                "mode": "screenshot",
                "image_base64": payload,
                "page": body.page or 1,
            }

        out_html = workdir / f"{src.stem}.html"
        outcome = run_officecli(["view", str(src), "html", "-o", str(out_html)], as_json=False)
        if not outcome.ok or not out_html.is_file():
            shutil.rmtree(workdir, ignore_errors=True)
            raise HTTPException(
                422,
                f"render_failed: {outcome.suggestion or outcome.message or outcome.error}",
            )
        _evict_if_needed()
        _sessions[session_id] = _PreviewSession(
            session_id=session_id,
            source=str(src),
            mode="html",
            artifact=out_html,
            workdir=workdir,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "mode": "html",
            "html_url": f"/office/preview/html/{session_id}",
        }
    except HTTPException:
        raise
    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        logger.warning("office preview failed: %s", exc)
        raise HTTPException(500, f"preview_failed: {exc}") from exc


@router.get("/html/{session_id}", response_class=HTMLResponse)
def serve_preview_html(session_id: str) -> HTMLResponse:
    """提供渲染后的 HTML（资源已内联，单文件即可展示）。"""
    session = _sessions.get(session_id)
    if session is None or session.mode != "html" or not session.artifact.is_file():
        raise HTTPException(404, "session_not_found: 会话不存在或已停止")
    return HTMLResponse(session.artifact.read_text(encoding="utf-8", errors="replace"))


@router.post("/stop")
def stop_preview(body: StopRequest) -> dict[str, Any]:
    """停止预览会话并清理临时目录。"""
    session = _sessions.pop(body.session_id, None)
    if session is not None:
        shutil.rmtree(session.workdir, ignore_errors=True)
    return {"ok": True, "stopped": session is not None}


@router.get("/sessions")
def list_sessions() -> dict[str, list[dict[str, Any]]]:
    """列出活跃预览会话（供前端状态展示）。"""
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "source": s.source,
                "mode": s.mode,
                "html_url": f"/office/preview/html/{s.session_id}" if s.mode == "html" else None,
            }
            for s in _sessions.values()
        ]
    }
