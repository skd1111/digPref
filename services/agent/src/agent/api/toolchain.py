"""Phase 18 工具链路径配置端点 —— 设置页「工具链」面板的后端。

GET  /toolchain → 当前配置（dict：python/node/pnpm/java/javac/tsc → 路径）
POST /toolchain → 保存配置（写入单文件 JSON，探测缓存失效）

路径仅存可执行文件位置（非凭证），不涉及敏感信息。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from agent.coding.toolchain import (
    clear_cache,
    load_toolchain_config,
    save_toolchain_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/toolchain", tags=["toolchain"])

_ALLOWED_KEYS = frozenset({"python", "node", "pnpm", "java", "javac", "tsc"})


class ToolchainSaveRequest(BaseModel):
    paths: dict[str, str]


@router.get("")
async def get_toolchain() -> dict:
    return {"paths": load_toolchain_config()}


@router.post("")
async def save_toolchain(body: ToolchainSaveRequest) -> dict:
    # 只接受白名单键 + 非空值，防止注入任意键
    cleaned = {
        k: v.strip()
        for k, v in body.paths.items()
        if k in _ALLOWED_KEYS and isinstance(v, str) and v.strip()
    }
    save_toolchain_config(cleaned)
    clear_cache()
    logger.info("toolchain config saved: %s", sorted(cleaned))
    return {"ok": True, "paths": cleaned}
