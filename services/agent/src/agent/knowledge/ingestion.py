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
import json
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

    async def _auto_classify(
        self, full_text: str, title: str, file_name: str, llm: Any | None
    ) -> str:
        """LLM 自动分类：从数据字典读取分类列表，分析文本前 2000 字判定分类。

        返回匹配到的分类 key；无法判定 / LLM 异常 / 无分类条目 → 返回空串。
        """
        from agent.knowledge.category import get_kb_categories

        categories = get_kb_categories()
        if not categories:
            return ""
        llm = llm or self._default_llm()
        if llm is None:
            return ""
        valid_keys = {c["key"] for c in categories}
        # 构造分类 prompt：列出所有分类的 label + description
        cat_lines = "\n".join(
            f"- {c['key']}: {c['label']}（{c['description']}）" for c in categories
        )
        prompt = (
            "请根据以下文档内容，判断它最属于哪个分类。只返回分类代码（key），不要其他文字。\n\n"
            f"可选分类：\n{cat_lines}\n\n"
            f"文档标题：{title}\n"
            f"文件名：{file_name}\n"
            f"文档内容（前 2000 字）：\n{full_text[:2000]}"
        )
        try:
            raw: str = await llm("kb_classify", prompt)
            result = (raw or "").strip().split()[0].strip().lower() if raw else ""
            if result in valid_keys:
                return result
            # LLM 可能返回 label 而非 key，尝试反向匹配
            for c in categories:
                if c["label"] in (raw or ""):
                    return str(c["key"])
            logger.warning("auto_classify: LLM 返回无法识别的分类 %r", raw)
            return ""
        except Exception as exc:
            # 限流 429 / 后端不可用等：告警级别（不再 debug 静默），便于定位“未分类”原因
            logger.warning("auto_classify skipped (LLM 不可用或限流): %s", exc)
            return ""

    async def classify_with_fallback(
        self, full_text: str, title: str, file_name: str, llm: Any | None = None
    ) -> tuple[str, str]:
        """分类判定：优先 LLM，失败（限流/不可用/无法判定）时按文件名启发式兜底。

        返回 ``(category, source)``，source ∈ {"llm", "heuristic", ""}。
        """
        category = await self._auto_classify(full_text, title, file_name, llm)
        if category:
            return category, "llm"
        try:
            from agent.knowledge.category import guess_category

            guess = guess_category(file_name, full_text)
        except Exception as exc:  # pragma: no cover - 兜底失败不应阻断
            logger.debug("guess_category failed: %s", exc)
            guess = ""
        if guess:
            logger.info("auto-classify fallback(启发式) file=%s → %s", file_name, guess)
            return guess, "heuristic"
        return "", ""

    async def classify_text(
        self, full_text: str, title: str, file_name: str
    ) -> tuple[str, str]:
        """对外的分类判定入口（导入前预判用；LLM 优先 + 启发式兜底）。

        返回 ``(category, source)``；无法判定 → ("", "")。
        """
        result, source = await self.classify_with_fallback(full_text, title, file_name, None)
        return result, source

    async def _extract_entities(
        self, parent_text: str, llm: Any | None
    ) -> list[dict[str, str]]:
        """LLM 从父块文本中抽取实体（从数据字典读实体类型列表）。

        返回 ``[{"entity_name": "张三", "entity_type": "person"}, ...]``。
        异常 / 无实体类型条目 / LLM 不可用 → 返回空列表（best-effort）。
        """
        from agent.knowledge.entity_types import get_entity_types

        entity_types = get_entity_types()
        if not entity_types:
            return []
        llm = llm or self._default_llm()
        if llm is None:
            return []
        valid_keys = {t["key"] for t in entity_types}
        type_lines = "\n".join(
            f"- {t['key']}: {t['label']}（{t['description']}）" for t in entity_types
        )
        prompt = (
            "请从以下文档片段中提取关键实体，按类型分组返回 JSON 格式。\n\n"
            f"实体类型：\n{type_lines}\n\n"
            "要求：\n"
            "1. 每类最多 5 个实体\n"
            "2. 只提取有实际意义的实体，忽略泛化词\n"
            "3. 直接返回 JSON，不要其他文字\n\n"
            f"文档片段：\n{parent_text[:2000]}"
        )
        try:
            raw = await llm("kb_entity_extract", prompt)
            if not raw or not raw.strip():
                return []
            # 提取 JSON（LLM 可能包裹在 ```json``` 中）
            text = raw.strip()
            if "```" in text:
                import re
                m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
                if m:
                    text = m.group(1).strip()
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                return []
            result: list[dict[str, str]] = []
            for type_key, names in parsed.items():
                if type_key not in valid_keys:
                    continue
                if not isinstance(names, list):
                    continue
                for name in names[:5]:
                    name_str = str(name).strip()
                    if name_str:
                        result.append(
                            {"entity_name": name_str, "entity_type": type_key}
                        )
            return result
        except Exception as exc:
            logger.debug("extract_entities skipped: %s", exc)
            return []

    async def ingest_file(
        self,
        path: str | Path,
        *,
        metadata: dict[str, Any] | None = None,
        category: str = "",
        doc_id: str | None = None,
        copy: bool = True,
        llm: Any | None = None,
        group_id: str | None = None,
        version: int = 1,
        is_active: bool = True,
    ) -> dict[str, Any]:
        """导入单个文件；返回文档摘要 dict。失败置 status=failed 并抛 IngestionError。

        版本化：group_id/version 标识该文件在「逻辑资料」中的版本；新版本传
        is_active=False 建档（索引期间旧生效版本仍可检索），就绪后由 activate_version
        原子切换为唯一生效版本（无空窗、无需重建索引）。
        """
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
            group_id=group_id,
            version=version,
            is_active=is_active,
        )
        try:
            from agent.doc_review.parser import parse_document

            parsed = await asyncio.to_thread(parse_document, str(src))
            full_text = getattr(parsed, "full_text", "") or ""
            if not full_text.strip():
                raise IngestionError("未提取到文本（可能为扫描件）")

            # 自动分类（category 为空 + 总开关 + 子开关均开启时判定；LLM 失败走启发式兜底）
            if (
                not category
                and settings.rag_llm_enhance_enabled
                and settings.rag_auto_classify_enabled
            ):
                auto_cat, cat_source = await self.classify_with_fallback(
                    full_text, src.stem, src.name, llm
                )
                if auto_cat:
                    category = auto_cat
                    self.storage.set_category(doc_id, category)
                    logger.info(
                        "auto-classified doc_id=%s → %s (source=%s)",
                        doc_id,
                        category,
                        cat_source,
                    )

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
            # 实体抽取（总开关 + 子开关均开启时按父块逐块抽取）
            ent_count = 0
            if (
                settings.rag_llm_enhance_enabled
                and settings.rag_entity_extract_enabled
            ):
                for p in parents:
                    ents = await self._extract_entities(p.text, llm)
                    if ents:
                        p_seq = parent_map.get(p.ord, 0)
                        ent_count += self.storage.upsert_entities(
                            doc_id, ents, p_seq
                        )
            self.storage.set_doc_status(
                doc_id, "ready", chunk_count=len(children)
            )
            # 新版本安全切换：索引就绪后才把该版本设为生效（就绪前旧版本仍可检索，无空窗）；
            # 全新单版本上传时为幂等（自身即唯一生效版本）。
            try:
                self.storage.activate_version(doc_id)
            except Exception:  # pragma: no cover - 切换失败不阻断入库结果
                logger.debug("activate_version failed doc_id=%s", doc_id, exc_info=True)
            try:
                from agent.audit.store import audit

                await audit(
                    "knowledge.doc_imported",
                    {
                        "doc_id": doc_id,
                        "file_name": src.name,
                        "chunks": len(children),
                        "group_id": group_id or doc_id,
                        "version": int(version),
                    },
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
