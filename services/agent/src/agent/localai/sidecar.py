"""localai.sidecar —— Sidecar HTTP 健康检查。

Phase 4 V0：轻量 HTTP 探测，验证本地 llama-server 是否在线。
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


async def check_health(base_url: str, timeout: float = 3.0) -> bool:
    """探测本地模型服务是否在线（GET /v1/models）。

    Args:
        base_url: 模型服务地址（如 http://127.0.0.1:8081/v1）
        timeout: 超时秒数

    Returns:
        True 如果服务响应 200
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(f"{base_url.rstrip('/')}/models")
            return r.status_code == 200
    except Exception as e:
        logger.debug("localai health check failed: %s %s", base_url, e)
        return False


async def check_all_models(
    small_url: str | None = None,
    vision_url: str | None = None,
    embedding_url: str | None = None,
) -> dict:
    """批量检查所有本地模型状态。

    Returns:
        {"small": bool, "vision": bool, "embedding": bool}
    """
    results = {}
    if small_url:
        results["small"] = await check_health(small_url)
    if vision_url:
        results["vision"] = await check_health(vision_url)
    if embedding_url:
        results["embedding"] = await check_health(embedding_url)
    return results
