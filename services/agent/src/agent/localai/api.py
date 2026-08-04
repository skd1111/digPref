"""localai.api —— Phase 4 V0 本地模型 FastAPI 路由。"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from agent.config import settings
from agent.localai.sidecar import check_all_models

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/localai", tags=["localai"])


@router.get("/status")
async def localai_status():
    """查询三个本地模型的状态。

    Returns:
        {"small": bool, "vision": bool, "embedding": bool}
    """
    return await check_all_models(
        small_url=settings.local_small_base_url,
        vision_url=settings.local_vision_base_url,
        embedding_url=settings.local_embedding_base_url,
    )


@router.get("/health")
async def localai_health():
    """健康检查：三个模型是否全部在线。"""
    status = await check_all_models(
        small_url=settings.local_small_base_url,
        vision_url=settings.local_vision_base_url,
        embedding_url=settings.local_embedding_base_url,
    )
    all_healthy = all(status.values()) if status else False
    return {
        "healthy": all_healthy,
        "models": status,
    }
