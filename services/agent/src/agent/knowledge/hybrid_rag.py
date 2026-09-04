"""knowledge.hybrid_rag —— 本地混合检索引擎（审核专家 + 聊天共用）。

三段式召回，全部在 SQLite 引擎内 + 进程内 ONNX 完成，零外部服务：
    1. 稀疏通道 BM25：SQLite FTS5（jieba 分词）原生 bm25()，精准匹配术语/条款编号；
    2. 稠密通道 Vector：sqlite-vec 余弦，理解语义/同义词；
    3. 融合 RRF：Reciprocal Rank Fusion 合并两通道排名（免调参），再经 ONNX
       cross-encoder reranker 重排取 Top-K（检索后补救，模型缺失自动 no-op）。

small-to-big：命中子块后回喂父块给 LLM，补全上下文；每块携带层级标题前缀
（chunker 已拼），缓解长文档「迷失在中间」。

降级红线：embedding 不可用 -> 纯 BM25；FTS5 不可用 -> 纯向量；两者皆空但库非空
-> LIKE 兜底；reranker 不可用 -> 保持 RRF 序。任一故障返空/原序，绝不抛出阻塞上层。

LLM 增强阶段（HyDE / Query Expansion）为可插拔 seam，默认关；开启且注入 llm 时才生效。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agent.config import settings
from agent.knowledge import tokenizer as tk

logger = logging.getLogger("agent.knowledge.hybrid_rag")


@dataclass
class HybridHit:
    """一条混合检索命中（子块 + 回喂的父块 + 溯源信息）。"""

    seq: int
    chunk_id: str
    doc_id: str
    content: str  # 子块原文（聚焦，用于高亮/定位）
    parent_content: str  # 父块原文（small-to-big，喂 LLM）；无父块时等于 content
    heading_path: str = ""
    page_no: int = 1
    category: str = ""
    source_type: str = "markdown"
    file_name: str = ""
    doc_title: str = ""
    score: float = 0.0  # RRF 融合分或 rerank 分
    matched: list[str] = field(default_factory=list)  # BM25 命中词（前端高亮）

    @property
    def source(self) -> str:
        """人类可读溯源串：文件名（第X页 · 章节路径）。"""
        name = self.file_name or self.doc_title or self.doc_id
        parts = [f"第{self.page_no}页"] if self.page_no else []
        if self.heading_path:
            parts.append(self.heading_path)
        suffix = f"（{' · '.join(parts)}）" if parts else ""
        return f"{name}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "content": self.content,
            "parent_content": self.parent_content,
            "heading_path": self.heading_path,
            "page_no": self.page_no,
            "category": self.category,
            "source_type": self.source_type,
            "file_name": self.file_name,
            "doc_title": self.doc_title,
            "score": self.score,
            "source": self.source,
            "matched": self.matched,
        }


# LLM 调用签名（与 doc_review.llm.LLMFunc 兼容：kind, prompt -> text）
LLMFunc = Callable[[str, str], Awaitable[str]]


class HybridRetriever:
    """FTS5 BM25 + 向量 + RRF + rerank 的混合检索器。

    embedding / reranker 可注入（测试替身）；缺省走统一入口（进程内 ONNX）。
    """

    def __init__(
        self,
        storage: Any | None = None,
        embedding: Any | None = None,
        reranker: Any | None = None,
    ) -> None:
        self._storage = storage
        self._embedding = embedding
        self._reranker = reranker
        self._llm: Any | None = None

    # ---- 依赖懒解析 ---------------------------------------------------------

    @property
    def storage(self) -> Any:
        if self._storage is None:
            from agent.knowledge.storage import get_default_storage

            self._storage = get_default_storage()
        return self._storage

    def _get_embedding(self) -> Any | None:
        if self._embedding is not None:
            return self._embedding
        from agent.knowledge.retriever import build_default_embedding_client

        self._embedding = build_default_embedding_client()
        return self._embedding

    def _get_reranker(self) -> Any | None:
        if self._reranker is not None:
            return self._reranker
        try:
            from agent.knowledge.reranker import get_reranker_client

            self._reranker = get_reranker_client()
        except Exception as exc:  # pragma: no cover
            logger.debug("reranker unavailable: %s", exc)
            self._reranker = None
        return self._reranker

    # ---- LLM 增强 seam（默认关；开启时用「已启用模型」）------------------

    def _default_llm(self) -> Any | None:
        """启用 LLM 增强但未注入 llm 时，用已启用模型链（LMRouter.generate_review）。"""
        if self._llm is not None:
            return self._llm
        try:
            from agent.knowledge.retriever import build_default_rag_llm

            self._llm = build_default_rag_llm()
        except Exception as exc:  # pragma: no cover
            logger.debug("rag default llm unavailable: %s", exc)
            self._llm = None
        return self._llm

    async def _maybe_hyde(self, query: str, llm: LLMFunc | None) -> str:
        """HyDE：LLM 先生成假设性文档，用其文本作向量查询（弥合语义鸿沟）。"""
        # 总开关关闭 → 只走本地混合检索，绝不发起大模型调用
        if not settings.rag_llm_enhance_enabled or not settings.rag_hyde_enabled:
            return query
        llm = llm or self._default_llm()
        if llm is None:
            return query
        try:
            hypo = await llm(
                "kb_hyde",
                f"请就下列问题写一段可能出自企业制度/法规文档的专业回答（只写正文，150字内）：\n{query}",
            )
            hypo = (hypo or "").strip()
            return f"{query}\n{hypo}" if hypo else query
        except Exception as exc:
            logger.debug("hyde skipped: %s", exc)
            return query

    async def _maybe_expand(self, query: str, llm: LLMFunc | None) -> list[str]:
        """Query Expansion：LLM 输出多个检索视角词，分别 BM25 后合并。"""
        # 总开关关闭 → 只走本地混合检索，绝不发起大模型调用
        if not settings.rag_llm_enhance_enabled or not settings.rag_query_expansion_enabled:
            return [query]
        llm = llm or self._default_llm()
        if llm is None:
            return [query]
        try:
            raw = await llm(
                "kb_expand",
                f"把下面的检索需求改写成 3 个不同表述（每行一个，不要编号）：\n{query}",
            )
            variants = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()][:3]
            return [query, *variants] if variants else [query]
        except Exception as exc:
            logger.debug("query expansion skipped: %s", exc)
            return [query]

    # ---- 主检索 -------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        filter: dict[str, Any] | None = None,
        expand_parent: bool = True,
        llm: LLMFunc | None = None,
    ) -> list[HybridHit]:
        """混合检索 Top-K。任何通道故障都 best-effort 降级，不抛出。"""
        t0 = time.perf_counter()
        query = (query or "").strip()
        if not query:
            return []
        # 库空短路：避免每条聊天白白触发 ONNX embedding / SQL 扫描
        try:
            if not self.storage.has_chunks():
                return []
        except Exception as exc:
            logger.debug("has_chunks guard failed: %s", exc)
        top_k = int(top_k or settings.rag_top_k)
        cand_n = max(top_k, top_k * int(settings.rag_candidate_multiplier))

        # 检索前补救（seam，默认关）
        vec_query = await self._maybe_hyde(query, llm)
        bm25_queries = await self._maybe_expand(query, llm)

        # --- 稀疏通道：FTS5 BM25（多查询变体合并，各自排名取最优）---
        bm25_rank: dict[int, int] = {}
        if settings.rag_bm25_enabled:
            for q in bm25_queries:
                try:
                    hits = self.storage.search_by_fts(q, limit=cand_n, filter=filter)
                except Exception as exc:
                    logger.debug("bm25 channel failed: %s", exc)
                    hits = []
                for rank, h in enumerate(hits):
                    seq = int(h["seq"])
                    if seq not in bm25_rank or rank < bm25_rank[seq]:
                        bm25_rank[seq] = rank

        # --- 稠密通道：向量余弦 ---
        vec_rank: dict[int, int] = {}
        if settings.rag_vector_enabled:
            embedding = self._get_embedding()
            qvec: list[float] = []
            if embedding is not None:
                try:
                    qvec = await embedding.embed(vec_query)
                except Exception as exc:
                    logger.debug("embed failed: %s", exc)
                    qvec = []
            if qvec and any(qvec):
                try:
                    vhits = self.storage.search_by_vector(qvec, top_k=cand_n, filter=filter)
                except Exception as exc:
                    logger.debug("vector channel failed: %s", exc)
                    vhits = []
                for rank, h in enumerate(vhits):
                    vec_rank[int(h["seq"])] = rank

        # --- RRF 融合 ---
        rrf_k = int(settings.rag_rrf_k)
        fused: dict[int, float] = {}
        for seq, rank in bm25_rank.items():
            fused[seq] = fused.get(seq, 0.0) + 1.0 / (rrf_k + rank + 1)
        for seq, rank in vec_rank.items():
            fused[seq] = fused.get(seq, 0.0) + 1.0 / (rrf_k + rank + 1)

        # 两通道皆空但库非空 -> LIKE 兜底
        if not fused:
            try:
                fb = self.storage.search_by_text(query, limit=top_k, filter=filter)
            except Exception as exc:
                logger.debug("like fallback failed: %s", exc)
                fb = []
            fused = {int(h["seq"]): 0.0 for h in fb}
            if not fused:
                logger.info("hybrid_search empty query_chars=%d", len(query))
                return []

        # 按 RRF 分排序取重排候选
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        rerank_n = int(settings.rag_rerank_top_n)
        candidates = (
            ordered[: max(top_k, rerank_n)] if settings.rag_rerank_enabled else ordered[:top_k]
        )

        # --- 检索后补救：ONNX reranker 重排（不可用则保持 RRF 序）---
        cand_seqs = [seq for seq, _ in candidates]
        chunks = self.storage.get_chunks_by_seq(cand_seqs)
        # 先取父块（rerank 用父块文本，信息更全）
        parent_map = self._load_parents(chunks) if expand_parent else {}
        score_by_seq = dict(candidates)
        if settings.rag_rerank_enabled and cand_seqs:
            reranker = self._get_reranker()
            if reranker is not None:
                docs: list[str] = []
                for s in cand_seqs:
                    ch = chunks.get(s)
                    if ch is None:
                        docs.append("")
                        continue
                    pseq = (ch.metadata or {}).get("parent_seq")
                    parent = parent_map.get(pseq) if pseq is not None else None
                    docs.append(parent or ch.content)
                try:
                    rscores = await reranker.rerank(query, docs)
                except Exception as exc:
                    logger.debug("rerank failed: %s", exc)
                    rscores = None
                if rscores and len(rscores) == len(cand_seqs):
                    score_by_seq = dict(zip(cand_seqs, rscores))
                    cand_seqs = sorted(cand_seqs, key=lambda s: score_by_seq[s], reverse=True)

        # --- 组装 Top-K ---
        qtokens = set(tk.tokenize(query))
        doc_cache: dict[str, dict[str, Any]] = {}
        results: list[HybridHit] = []
        for seq in cand_seqs[:top_k]:
            chunk = chunks.get(seq)
            if chunk is None:
                continue
            meta = chunk.metadata or {}
            parent_seq = meta.get("parent_seq")
            parent_content = (
                parent_map.get(int(parent_seq)) if parent_seq is not None else None
            ) or chunk.content
            doc_id = str(chunk.doc_id)
            if doc_id not in doc_cache:
                try:
                    doc_cache[doc_id] = self.storage.get_doc(doc_id) or {}
                except Exception:
                    doc_cache[doc_id] = {}
            doc = doc_cache[doc_id]
            matched = [t for t in qtokens if t and t in chunk.content]
            results.append(
                HybridHit(
                    seq=seq,
                    chunk_id=str(chunk.id),
                    doc_id=doc_id,
                    content=chunk.content,
                    parent_content=parent_content,
                    heading_path=str(meta.get("heading_path", "") or ""),
                    page_no=int(meta.get("page_no", 1) or 1),
                    category=str(meta.get("category", "") or ""),
                    source_type=str(meta.get("source_type", "markdown") or "markdown"),
                    file_name=str(doc.get("file_name", "") or ""),
                    doc_title=str(doc.get("title", "") or ""),
                    score=float(score_by_seq.get(seq, 0.0)),
                    matched=matched[:8],
                )
            )
        logger.info(
            "hybrid_search query_chars=%d bm25=%d vec=%d fused=%d rerank=%s hits=%d elapsed=%.1fms",
            len(query),
            len(bm25_rank),
            len(vec_rank),
            len(fused),
            settings.rag_rerank_enabled,
            len(results),
            (time.perf_counter() - t0) * 1000,
        )
        return results

    def _load_parents(self, chunks: dict[int, Any]) -> dict[int, str]:
        """批量取命中子块对应的父块原文（{parent_seq: content}）。"""
        parent_seqs: set[int] = set()
        for c in chunks.values():
            ps = (c.metadata or {}).get("parent_seq")
            if ps is not None:
                parent_seqs.add(int(ps))
        if not parent_seqs:
            return {}
        try:
            parents = self.storage.get_parents_by_seq(list(parent_seqs))
        except Exception as exc:
            logger.debug("load parents failed: %s", exc)
            return {}
        return {int(seq): str(p.get("content", "")) for seq, p in parents.items()}


# ---- 单例 --------------------------------------------------------------------

_default_retriever: HybridRetriever | None = None


def get_hybrid_retriever() -> HybridRetriever:
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = HybridRetriever()
    return _default_retriever


def reset_hybrid_retriever() -> None:
    global _default_retriever
    _default_retriever = None
