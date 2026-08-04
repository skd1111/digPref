"""knowledge.retriever —— Phase 4 V1 RAG 检索（占位恢复版）。

仅保留最小接口定义以满足其他模块 import 依赖。
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbeddingClientProto(Protocol):
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def build_default_embedding_client():
    return None


class RAGRetriever:
    """RAG 检索器（占位实现）。"""

    def __init__(self, storage, embedding=None, *, top_k: int = 3,
                 similarity_threshold: float = 0.0, max_prompt_chars: int = 2000):
        self.storage = storage
        self.embedding = embedding
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold
        self.max_prompt_chars = max_prompt_chars

    async def retrieve(self, query: str, **kwargs) -> Any:
        from agent.knowledge.models import RAGContext
        return RAGContext(query=query, backend="stub")

    def format_for_llm(self, results) -> str:
        return ""


_default_retriever: RAGRetriever | None = None


def get_default_retriever() -> RAGRetriever:
    global _default_retriever
    if _default_retriever is None:
        from agent.knowledge.storage import get_default_storage
        _default_retriever = RAGRetriever(get_default_storage(), None)
    return _default_retriever


def reset_default_retriever() -> None:
    global _default_retriever
    _default_retriever = None