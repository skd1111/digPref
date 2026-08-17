"""工作空间路径配置端点 —— 设置页「工作空间」面板的后端。

GET  /workspace → 当前工作空间（生效路径 + 是否自定义 + 默认路径）
POST /workspace → 保存自定义路径（空串 = 恢复默认）

底层规则（用户要求 2026-08-17）：智能体运行中创建的任何文件默认都落
当前工作空间内并按类型自动分类建目录；用户显式指定输出目录时尊重用户。
路径仅存目录位置（非凭证），不涉及敏感信息。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
            raise HTTPException(
                status_code=400, detail=f"工作空间路径不可用：{exc}"
            ) from exc
        save_workspace_override(str(resolved))
        logger.info("workspace override saved: %s", resolved)
    else:
        save_workspace_override(None)
        logger.info("workspace override cleared (restore default)")
    return {"ok": True, **_current()}
