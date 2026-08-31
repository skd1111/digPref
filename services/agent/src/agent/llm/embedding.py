"""LocalEmbeddingClient —— 本地 Embedding 模型客户端。

Phase 4 V0：为外部 KB 检索提供向量化能力。
2026-08-31：新增统一构建入口 —— 进程内 ONNX 向量模型优先（随安装包分发，
纯 CPU 无外部服务依赖），显式配置 base_url 时改走外置 OpenAI 兼容端点。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("agent.llm.embedding")

# 本地向量模型默认名（model/bge-small-zh-v1.5-onnx 下的量化 ONNX 模型）
DEFAULT_EMBEDDING_MODEL = "bge-small-zh-v1.5"


def build_default_embedding_client() -> Any | None:
    """构建默认向量客户端（接口一致：embed / embed_batch / health_check）。

    优先级：
        1. settings.local_embedding_base_url → 外置 OpenAI 兼容端点（HTTP）
        2. 进程内 ONNX（bge-small-zh-v1.5 量化版）——模型文件缺失时
           客户端 health_check 返 False，调用方静默回退（零向量契约）。
    """
    from agent.config import settings

    if settings.local_embedding_base_url:
        return LocalEmbeddingClient(
            base_url=settings.local_embedding_base_url,
            model=settings.local_embedding_model or DEFAULT_EMBEDDING_MODEL,
            dimensions=settings.local_embedding_dim,
        )
    from agent.llm.onnx_embedding import get_onnx_embedding_client

    return get_onnx_embedding_client()


class LocalEmbeddingUnavailableError(Exception):
    """本地 Embedding 模型不可达。"""


class LocalEmbeddingClient:
    """本地 Embedding 模型客户端 — OpenAI Embeddings API 兼容。"""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8083/v1",
        model: str = "bge-small-zh-v1.5",
        dimensions: int = 384,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions

    async def _embed_request(
        self,
        input_texts: str | list[str],
        timeout: float = 10.0,
    ) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_texts,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(f"{self.base_url}/embeddings", json=payload)
                r.raise_for_status()
                body = r.json()
                return [item["embedding"] for item in body["data"]]
        except httpx.ConnectError as exc:
            logger.debug("local_embedding: connection refused at %s", self.base_url)
            raise LocalEmbeddingUnavailableError(
                f"Local embedding model not reachable at {self.base_url}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.debug("local_embedding: request timed out")
            raise LocalEmbeddingUnavailableError("Local embedding model request timed out") from exc
        except (KeyError, IndexError) as exc:
            logger.debug("local_embedding: unexpected response shape: %s", exc)
            raise LocalEmbeddingUnavailableError(
                "Local embedding model returned unexpected response"
            ) from exc

    # ---- Public API -------------------------------------------------------

    async def embed(self, text: str) -> list[float]:
        """单文本向量化。

        Returns:
            384 维浮点向量；失败时返回零向量
        """
        try:
            results = await self._embed_request(text, timeout=5.0)
            return results[0] if results else [0.0] * self.dimensions
        except (LocalEmbeddingUnavailableError, Exception) as e:
            logger.debug("local_embedding: embed failed: %s", e)
            return [0.0] * self.dimensions

    async def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """批量向量化。

        Returns:
            与输入等长的向量列表；单个失败项返回零向量
        """
        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                batch_results = await self._embed_request(batch, timeout=15.0)
                results.extend(batch_results)
            except (LocalEmbeddingUnavailableError, Exception) as e:
                logger.debug("local_embedding: embed_batch failed at %d: %s", i, e)
                results.extend([[0.0] * self.dimensions] * len(batch))
        return results

    # ---- Health -----------------------------------------------------------

    async def health_check(self) -> bool:
        """探测 embedding 模型服务是否在线。"""
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{self.base_url}/models")
                return r.status_code == 200
        except Exception:
            return False
