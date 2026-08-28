"""工作空间路径配置端点 —— 设置页「工作空间」面板的后端。

GET  /workspace → 当前工作空间（生效路径 + 是否自定义 + 默认路径）
POST /workspace → 保存自定义路径（空串 = 恢复默认）
GET  /workspace/tasks/{task_id}         → 任务目录文件清单（产物/中间文件，2026-08-26）
POST /workspace/tasks/{task_id}/cleanup → 验收后清理中间文件（保留产物）

底层规则（用户要求 2026-08-17）：智能体运行中创建的任何文件默认都落
当前工作空间内并按类型自动分类建目录；用户显式指定输出目录时尊重用户。
路径仅存目录位置（非凭证），不涉及敏感信息。
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.paths import (
    data_root,
    load_workspace_override,
    save_workspace_override,
    workspace_dir,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["workspace"])


class WorkspaceSaveRequest(BaseModel):
    path: str = ""


def _current() -> dict[str, Any]:
    override = load_workspace_override()
    return {
        "path": str(workspace_dir()),
        "custom": override,
        "default": str((data_root() / "workspace").resolve(strict=False)),
    }


@router.get("")
async def get_workspace() -> dict[str, Any]:
    return _current()


@router.post("")
async def save_workspace(body: WorkspaceSaveRequest) -> dict[str, Any]:
    raw = body.path.strip()
    if raw:
        # 校验可解析 + 可创建（防 UNC / 非法盘符等直接落盘失败的路径）
        p = Path(raw).expanduser()
        if raw.startswith("\\\\") or raw.startswith("//"):
            raise HTTPException(status_code=400, detail="UNC 路径不可作为工作空间")
        try:
            resolved = p.resolve(strict=False)
            resolved.mkdir(parents=True, exist_ok=True)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=f"工作空间路径不可用：{exc}") from exc
        save_workspace_override(str(resolved))
        logger.info("workspace override saved: %s", resolved)
    else:
        save_workspace_override(None)
        logger.info("workspace override cleared (restore default)")
    return {"ok": True, **_current()}


# ---- 任务级工作目录：验收与清理（2026-08-26）--------------------------------


class TaskCleanupRequest(BaseModel):
    # 额外保留的文件（绝对路径）；台账产物默认保留，无需重复传
    keep: list[str] = Field(default_factory=list)


def _resolve_task_dir_or_404(task_id: str) -> Path:
    """解析任务目录并防目录穿越：必须位于当前工作空间的 tasks/ 内。"""
    from agent.paths import task_dir

    tid = task_id.strip()
    if not tid:
        raise HTTPException(status_code=400, detail="task_id 不能为空")
    d = task_dir(tid, ensure=False)
    if d is None:
        raise HTTPException(status_code=400, detail="task_id 无效")
    tasks_root = (workspace_dir(ensure=False) / "tasks").resolve(strict=False)
    try:
        d.relative_to(tasks_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="任务目录越出工作空间，拒绝操作") from exc
    return d


def _list_task_files(root: Path) -> list[str]:
    """递归列出任务目录内所有文件（规范化绝对路径，目录本身不列）。"""
    if not root.is_dir():
        return []
    return sorted(str(p.resolve(strict=False)) for p in root.rglob("*") if p.is_file())


@router.get("/tasks/{task_id}")
async def get_task_files(task_id: str) -> dict[str, Any]:
    """任务目录文件清单：产物（台账记录且实际存在）与中间文件。"""
    from agent.paths import ledger_read

    d = _resolve_task_dir_or_404(task_id)
    files = _list_task_files(d)
    file_set = set(files)
    artifacts = [p for p in ledger_read(task_id)["artifacts"] if p in file_set]
    artifact_set = set(artifacts)
    intermediates = [f for f in files if f not in artifact_set]
    return {
        "task_dir": str(d),
        "task_dir_exists": d.is_dir(),
        "artifacts": artifacts,
        "intermediates": intermediates,
    }


@router.post("/tasks/{task_id}/cleanup")
async def cleanup_task_files(task_id: str, body: TaskCleanupRequest) -> dict[str, Any]:
    """验收后清理：删除任务目录内除产物外的所有文件；目录清空后连目录一起删。

    保留集 = 台账 artifacts ∪ body.keep（均规范化后比对）；
    删除失败（文件被占用等）的文件计入 kept，不阻断其余清理。
    """
    from agent.paths import ledger_read

    d = _resolve_task_dir_or_404(task_id)
    if not d.is_dir():
        return {"ok": True, "deleted": [], "kept": [], "task_dir_removed": False}

    keep: set[Path] = set()
    for p in ledger_read(task_id)["artifacts"]:
        keep.add(Path(p).resolve(strict=False))
    for p in body.keep:
        if str(p).strip():
            keep.add(Path(str(p).strip()).resolve(strict=False))

    deleted: list[str] = []
    kept: list[str] = []
    for f in sorted(d.rglob("*")):
        if not f.is_file():
            continue
        if f.resolve(strict=False) in keep:
            kept.append(str(f))
            continue
        try:
            f.unlink()
            deleted.append(str(f))
        except OSError:
            kept.append(str(f))  # 删不掉（占用/权限）→ 保留，不阻断
    # 自底向上清空子目录；任务目录本身也只在变空时删除（防误删还有残余的目录）
    for sub in sorted(
        (p for p in d.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        with contextlib.suppress(OSError):
            sub.rmdir()
    with contextlib.suppress(OSError):
        d.rmdir()
    logger.info("task cleanup: %s deleted=%d kept=%d", task_id, len(deleted), len(kept))
    return {
        "ok": True,
        "deleted": deleted,
        "kept": kept,
        "task_dir_removed": not d.exists(),
    }
