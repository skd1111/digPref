"""knowledge.ingestion —— 文件上传导入编排（复制 → 解析 → 分块 → 向量化 → 入库）。

审核专家「上传参考资料」与聊天知识库共用此编排：
    1. 校验扩展名 + 大小（rag_max_file_mb）；
    2. 复制源文件到 kb_files_dir()（迁移自包含，不依赖用户原路径）；
    3. doc_review.parser.parse_document 解析（pdf/docx/doc/txt/md/html/xlsx/pptx）；
    4. chunker 父子两层分块（标题上下文前缀 + 页码元数据）；
    5. 进程内 ONNX embedding 批量向量化子块 index_text；
    6. storage 落库：parents + chunks(+FTS5 tokens +vec0) + docs 状态机 + kb_meta。

红线：
    - embedding 不可用 → 只落 BM25/文本通道（向量为空），检索退化不中断；
    - 敏感素材只在内网本地库，向量/FTS 同库同机；
    - LLM 上下文前缀（Contextual Retrieval）为可插拔 seam，默认关（rag_llm_contextual_enabled）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from agent.config import settings

logger = logging.getLogger(__name__)

_SUPPORTED_SUFFIXES = {
    ".pdf",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".html",
    ".htm",
    ".xlsx",
    ".pptx",
}
_UNSAFE_NAME_RE = re.compile(r"[^\w.\u4e00-\u9fff-]+")


class IngestionError(Exception):
    pass


def _safe_name(name: str) -> str:
    return _UNSAFE_NAME_RE.sub("_", name)[:120] or "file"


def _page_index(parsed: Any) -> list[tuple[int, int]]:
    """从 ParsedDocument 的块偏移构造 (字符偏移 -> 页码) 升序表。"""
    idx: list[tuple[int, int]] = []
    for page in getattr(parsed, "pages", []) or []:
        for block in getattr(page, "blocks", []) or []:
            idx.append(
                (int(getattr(block, "start", 0) or 0), int(getattr(page, "page_no", 1) or 1))
            )
    idx.sort(key=lambda x: x[0])
    return idx


class KnowledgeIngestion:
    """导入编排器（文件 -> 分块 -> 向量 -> 入库）。"""

    def __init__(self, storage: Any, embedding: Any | None = None) -> None:
        self.storage = storage
        self.embedding = embedding
        self._llm: Any | None = None

    def _get_embedding(self) -> Any | None:
        if self.embedding is None:
            from agent.knowledge.retriever import build_default_embedding_client

            self.embedding = build_default_embedding_client()
        return self.embedding

    def _default_llm(self) -> Any | None:
        """启用 LLM 上下文前缀但未注入 llm 时，用已启用模型链（LMRouter.generate_review）。"""
        if self._llm is not None:
            return self._llm
        try:
            from agent.knowledge.retriever import build_default_rag_llm

            self._llm = build_default_rag_llm()
        except Exception as exc:  # pragma: no cover
            logger.debug("rag default llm unavailable: %s", exc)
            self._llm = None
        return self._llm

    async def _llm_contextual_prefix(self, parent_text: str, llm: Any | None) -> str:
        """seam：LLM 为父块生成上下文前缀（默认关；开启时用已启用模型）。"""
        # 总开关关闭 → 入库全程零大模型调用
        if not settings.rag_llm_enhance_enabled or not settings.rag_llm_contextual_enabled:
            return ""
        llm = llm or self._default_llm()
        if llm is None:
            return ""
        try:
            prefix = await llm(
                "kb_contextual",
                "用一句话说明下面这段文字的主题与所属章节（只写这句话，30字内）：\n"
                + parent_text[:1500],
            )
            return (prefix or "").strip()
        except Exception as exc:  # pragma: no cover - 默认关
            logger.debug("llm contextual prefix skipped: %s", exc)
            return ""

    async def ingest_file(
        self,
        path: str | Path,
        *,
        metadata: dict[str, Any] | None = None,
        category: str = "",
        doc_id: str | None = None,
        copy: bool = True,
        llm: Any | None = None,
    ) -> dict[str, Any]:
        """导入单个文件；返回文档摘要 dict。失败置 status=failed 并抛 IngestionError。"""
        src = Path(path)
        if not src.exists():
            raise IngestionError(f"文件不存在: {src}")
        suffix = src.suffix.lower()
        if suffix not in _SUPPORTED_SUFFIXES:
            raise IngestionError(
                f"不支持的格式: {suffix}（支持 pdf/docx/doc/txt/md/csv/html/xlsx/pptx）"
            )
        size = src.stat().st_size
        max_bytes = int(settings.rag_max_file_mb) * 1024 * 1024
        if size > max_bytes:
            raise IngestionError(
                f"文件过大：{size / 1024 / 1024:.1f}MB 超过上限 {settings.rag_max_file_mb}MB"
            )

        doc_id = doc_id or uuid.uuid4().hex
        meta = dict(metadata or {})
        category = str(category or meta.get("category", "") or "")
        from agent.knowledge.rag_config import kb_files_dir

        # 复制入库（迁移自包含）
        relpath: str | None = None
        if copy:
            try:
                files_dir = kb_files_dir()
                files_dir.mkdir(parents=True, exist_ok=True)
                stored_name = f"{doc_id}_{_safe_name(src.name)}"
                shutil.copy2(str(src), str(files_dir / stored_name))
                relpath = stored_name
            except OSError as exc:
                logger.warning("ingest copy failed (%s): %s", src, exc)

        self.storage.insert_doc(
            doc_id=doc_id,
            title=src.stem,
            file_name=src.name,
            source_type=suffix.lstrip("."),
            source_relpath=relpath,
            category=category,
            size_bytes=size,
            status="indexing",
            metadata=meta,
        )
        try:
            from agent.doc_review.parser import parse_document

            parsed = await asyncio.to_thread(parse_document, str(src))
            full_text = getattr(parsed, "full_text", "") or ""
            if not full_text.strip():
                raise IngestionError("未提取到文本（可能为扫描件）")

            result = self._chunk(
                parsed,
                full_text,
                category=category,
                source_type=suffix.lstrip("."),
                file_name=src.name,
            )
            parents, children = result["parents"], result["children"]

            # LLM 上下文前缀（seam，默认关；总开关为硬闸）：按父块生成，拼到该父块下子块的索引文本
            prefix_by_parent: dict[int, str] = {}
            if settings.rag_llm_enhance_enabled and settings.rag_llm_contextual_enabled:
                for p in parents:
                    pfx = await self._llm_contextual_prefix(p.text, llm)
                    if pfx:
                        prefix_by_parent[p.ord] = pfx

            parent_map = self.storage.upsert_parents(doc_id, parents)
            await self._embed_and_store(
                doc_id, children, parent_map, prefix_by_parent, category, suffix.lstrip(".")
            )
            self.storage.set_doc_status(doc_id, "ready", chunk_count=len(children))
            try:
                from agent.audit.store import audit

                await audit(
                    "knowledge.doc_imported",
                    {"doc_id": doc_id, "file_name": src.name, "chunks": len(children)},
                )
            except Exception:
                logger.debug("knowledge audit write skipped", exc_info=True)
            logger.info(
                "ingest done doc_id=%s file=%s parents=%d children=%d",
                doc_id,
                src.name,
                len(parents),
                len(children),
            )
            return self.storage.get_doc(doc_id) or {"doc_id": doc_id, "status": "ready"}
        except Exception as exc:
            self.storage.set_doc_status(doc_id, "failed", error=str(exc)[:500])
            logger.warning("ingest failed doc_id=%s file=%s: %s", doc_id, src, exc)
            raise IngestionError(str(exc)) from exc

    def _chunk(
        self, parsed: Any, full_text: str, *, category: str, source_type: str, file_name: str
    ) -> dict[str, Any]:
        from agent.knowledge.chunker import chunk_text

        page_index = _page_index(parsed)
        result = chunk_text(
            full_text,
            chunk_size=int(settings.rag_chunk_size),
            overlap=float(settings.rag_chunk_overlap),
            parent_size=int(settings.rag_parent_size),
            contextual_prefix=bool(settings.rag_contextual_prefix_enabled),
            page_index=page_index,
            base_metadata={
                "category": category,
                "source_type": source_type,
                "file_name": file_name,
            },
        )
        return {"parents": result.parents, "children": result.children}

    async def _embed_and_store(
        self,
        doc_id: str,
        children: list[Any],
        parent_map: dict[int, int],
        prefix_by_parent: dict[int, str],
        category: str,
        source_type: str,
    ) -> None:
        from agent.knowledge.models import KnowledgeChunk

        embedding = self._get_embedding()
        contextual = bool(settings.rag_contextual_prefix_enabled)
        index_texts: list[str] = []
        for c in children:
            base = c.index_text(contextual_prefix=contextual)
            pfx = prefix_by_parent.get(c.parent_ord, "")
            index_texts.append(f"{pfx}\n{base}" if pfx else base)

        vectors: list[list[float]] = []
        if embedding is not None and index_texts:
            try:
                vectors = await embedding.embed_batch(index_texts)
            except Exception as exc:
                logger.warning("embed_batch failed, store BM25-only: %s", exc)
                vectors = []

        records: list[KnowledgeChunk] = []
        dim = 0
        for i, c in enumerate(children):
            vec = vectors[i] if i < len(vectors) else None
            if vec and any(vec):
                dim = len(vec)
            else:
                vec = None
            records.append(
                KnowledgeChunk(
                    id=f"{doc_id}#{c.ord}",
                    doc_id=doc_id,
                    seq=c.ord,
                    content=c.text,
                    token_count=c.token_count,
                    embedding=vec,
                    metadata={
                        "index_text": index_texts[i] if i < len(index_texts) else c.text,
                        "parent_seq": parent_map.get(c.parent_ord),
                        "heading_path": c.heading_path,
                        "page_no": c.page_no,
                        "category": category,
                        "source_type": source_type,
                    },
                )
            )
        self.storage.upsert_chunks(doc_id, records)
        if dim and embedding is not None:
            model = str(
                getattr(embedding, "model", "")
                or settings.local_embedding_model
                or "bge-small-zh-v1.5"
            )
            self.storage.set_meta(model, dim)

    # ---- 便捷入口（按格式；均委托 ingest_file）------------------------------

    async def reindex(self, *, on_progress: Any | None = None) -> dict[str, Any]:
        """基于库内子块原文重建向量（迁移到不同 embedding 模型/维度时自愈）。

        不依赖源文件原路径——原文已随子块入库。返回 {total, done, ok, reason?}。
        """
        embedding = self._get_embedding()
        if embedding is None:
            return {"total": 0, "done": 0, "ok": False, "reason": "embedding_unavailable"}
        items = self.storage.iter_chunks_for_reindex()
        total = len(items)
        if not total:
            self.storage.reset_stale_docs()
            return {"total": 0, "done": 0, "ok": True}
        texts = [str(it["index_text"]) for it in items]
        try:
            vecs = await embedding.embed_batch(texts)
        except Exception as exc:
            logger.warning("reindex embed_batch failed: %s", exc)
            return {"total": total, "done": 0, "ok": False, "reason": str(exc)[:200]}
        dim = 0
        done = 0
        for it, vec in zip(items, vecs):
            if vec and any(vec):
                dim = len(vec)
                self.storage.update_chunk_vector(int(it["seq"]), list(vec))
            done += 1
            if on_progress and done % 16 == 0:
                on_progress(done / total)
        if dim:
            model = str(
                getattr(embedding, "model", "")
                or settings.local_embedding_model
                or "bge-small-zh-v1.5"
            )
            self.storage.set_meta(model, dim)
        self.storage.reset_stale_docs()
        if on_progress:
            on_progress(1.0)
        logger.info("reindex done total=%d dim=%d", total, dim)
        return {"total": total, "done": done, "ok": True}

    async def ingest_markdown_file(
        self, path: Any, *, metadata: dict[str, Any] | None = None
    ) -> Any:
        return await self.ingest_file(path, metadata=metadata)

    async def ingest_pdf_file(self, path: Any, *, metadata: dict[str, Any] | None = None) -> Any:
        return await self.ingest_file(path, metadata=metadata)

    async def ingest_docx_file(self, path: Any, *, metadata: dict[str, Any] | None = None) -> Any:
        return await self.ingest_file(path, metadata=metadata)

    # ---- 同步入口（保留占位，随后续 Phase 推进）------------------------------

    async def sync_from_biznav(self) -> int:
        return 0

    async def sync_from_codenav(self) -> int:
        return 0


def build_default_ingestion() -> KnowledgeIngestion:
    from agent.knowledge.retriever import build_default_embedding_client
    from agent.knowledge.storage import get_default_storage

    return KnowledgeIngestion(get_default_storage(), build_default_embedding_client())
