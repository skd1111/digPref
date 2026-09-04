"""knowledge.retriever —— RAG 检索器（委托 hybrid_rag，产出 RAGContext）。

RAGRetriever.retrieve() 是聊天 rag_retrieve 节点与 /knowledge/v1/search 的统一入口：
调 HybridRetriever 做 FTS5 BM25 + 向量 + RRF + rerank 混合检索，把命中包装成
RetrievalResult 并拼出带编号引用的 formatted_prompt（供 system_prompt_addon 与
审核提示词共用，强制溯源、禁止编造条款）。

embedding 缺省走统一入口（进程内 ONNX bge 优先，显式配置走外置 HTTP）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol, cast, runtime_checkable

from agent.config import settings

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbeddingClientProto(Protocol):
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def build_default_embedding_client() -> Any | None:
    """统一向量客户端入口（进程内 ONNX 优先，显式 base_url 走外置 HTTP）。"""
    try:
        from agent.llm.embedding import build_default_embedding_client as _build

        return _build()
    except Exception as exc:  # pragma: no cover
        logger.debug("embedding client unavailable: %s", exc)
        return None


def build_default_rag_llm() -> Any:
    """RAG LLM 增强阶段（HyDE / 查询扩展 / 上下文前缀）的默认模型。

    走 LMRouter.generate_review —— 用「模型管理」里已启用的后端（router.db），
    按 settings.doc_review_llm_chain（默认 cloud→private，均需已启用）顺序调用，
    与 doc_review 审核同源，无需为 RAG 单独配置模型。返回 (kind, prompt) -> str。
    """
    from agent.llm.router import LMRouter
    from agent.llm.types import TaskKind

    async def _call(kind: str, prompt: str) -> str:
        return await LMRouter().generate_review(kind=cast("TaskKind", kind), prompt=prompt)

    return _call


def _citation_prompt(hits: list[Any], *, max_chars: int) -> str:
    """把命中拼成带编号引用的提示词片段（强制溯源，禁止编造）。"""
    if not hits:
        return ""
    lines: list[str] = ["## 知识库参考（本地混合检索）"]
    for i, h in enumerate(hits, 1):
        body = (h.parent_content or h.content or "").strip().replace("\n", " ")
        lines.append(f"[{i}] {h.source}：{body}")
    lines.append("")
    lines.append(
        "（引用要求：回答涉及上述事实时必须标注来源编号如 [1]；"
        "只能引用上述编号，禁止编造不存在的条款、页码或文件名。）"
    )
    text = "\n".join(lines)
    return text[:max_chars]


class RAGRetriever:
    """RAG 检索器（混合检索 + 编号引用提示词）。"""

    def __init__(
        self,
        storage: Any = None,
        embedding: Any = None,
        *,
        top_k: int | None = None,
        similarity_threshold: float = 0.0,
        max_prompt_chars: int = 4000,
        hybrid: Any | None = None,
    ) -> None:
        self.storage = storage
        self.embedding = embedding if embedding is not None else build_default_embedding_client()
        self.top_k = int(top_k or settings.rag_top_k)
        self.similarity_threshold = similarity_threshold
        self.max_prompt_chars = max_prompt_chars
        if hybrid is not None:
            self._hybrid = hybrid
        else:
            from agent.knowledge.hybrid_rag import HybridRetriever

            self._hybrid = HybridRetriever(storage=storage, embedding=self.embedding)

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        filter: dict[str, Any] | None = None,
        source_type_filter: Any = None,
        similarity_threshold: float | None = None,
        llm: Any | None = None,
        **_: Any,
    ) -> Any:
        """混合检索 -> RAGContext（results + formatted_prompt）。best-effort 不抛出。"""
        from agent.knowledge.models import KnowledgeChunk, RAGContext, RetrievalResult

        t0 = time.perf_counter()
        filt = dict(filter or {})
        if source_type_filter and "source_type" not in filt:
            filt["source_type"] = source_type_filter
        k = int(top_k or self.top_k)
        try:
            hits = await self._hybrid.search(query, top_k=k, filter=filt or None, llm=llm)
        except Exception as exc:
            logger.warning("rag retrieve failed: %s", exc)
            return RAGContext(query=query, backend="hybrid", elapsed_ms=0)

        results: list[RetrievalResult] = []
        for h in hits:
            chunk = KnowledgeChunk(
                id=h.chunk_id,
                doc_id=h.doc_id,
                seq=0,
                content=h.parent_content or h.content,
                metadata={
                    "child_content": h.content,
                    "heading_path": h.heading_path,
                    "page_no": h.page_no,
                    "category": h.category,
                    "matched": h.matched,
                    "source": h.source,
                    "seq": h.seq,
                },
            )
            results.append(
                RetrievalResult(
                    chunk=chunk,
                    similarity=float(h.score),
                    doc_title=h.doc_title or h.file_name,
                    source_type=h.source_type,
                    citation=h.source,
                )
            )
        formatted = _citation_prompt(hits, max_chars=self.max_prompt_chars)
        elapsed = int((time.perf_counter() - t0) * 1000)
        return RAGContext(
            query=query,
            results=results,
            formatted_prompt=formatted,
            elapsed_ms=elapsed,
            backend="hybrid",
        )

    def format_for_llm(self, results: Any) -> str:
        """把 RetrievalResult 列表拼成提示词片段（无溯源元数据时的兜底）。"""
        if not results:
            return ""
        lines = ["## 知识库参考（本地混合检索）"]
        for i, r in enumerate(results, 1):
            content = getattr(getattr(r, "chunk", None), "content", "") or ""
            lines.append(f"[{i}] {getattr(r, 'citation', '')}：{content.strip()}")
        return "\n".join(lines)[: self.max_prompt_chars]


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
