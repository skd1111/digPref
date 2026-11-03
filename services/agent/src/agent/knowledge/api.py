"""knowledge.api —— 知识库 FastAPI 路由（V0 外部适配器 + V1 本地混合检索）。

路由分组：
- /knowledge/status                V0 mock 状态（向后兼容）
- /knowledge/search                V0 外部 KB 检索（向后兼容）
- /knowledge/v1/status             V1 本地 KB 状态（文档/块/模型漂移/重建进度）
- /knowledge/v1/docs               V1 文档列表
- /knowledge/v1/docs/upload        V1 上传导入（后台分块+向量化，轮询状态）
- /knowledge/v1/docs/{id} DELETE    V1 删除（级联清 chunks+FTS+vec+parents）
- /knowledge/v1/search             V1 混合检索（FTS5 BM25 + 向量 + RRF + rerank）
- /knowledge/v1/reindex            V1 全量重建向量（模型漂移自愈）
- /knowledge/v1/config             V1 RAG 参数读写（落 kb.db，写后热应用）
- /knowledge/v1/sync/biznav|codenav  V1 同步（占位）

V0/V1 共存：V0 路由不动；新功能全部走 /v1 前缀。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.config import settings
from agent.knowledge import rag_config
from agent.knowledge.adapter import KBContext, build_kb_context
from agent.knowledge.ingestion import IngestionError, KnowledgeIngestion
from agent.knowledge.models import (
    KnowledgeStats,
    RAGContext,
)
from agent.knowledge.retriever import RAGRetriever
from agent.knowledge.storage import KnowledgeStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# ---- 单例 lazy ---------------------------------------------------------

_storage: KnowledgeStorage | None = None
_retriever: RAGRetriever | None = None
_ingestion: KnowledgeIngestion | None = None


def _get_storage() -> KnowledgeStorage:
    global _storage
    if _storage is None:
        from agent.knowledge.storage import get_default_storage

        _storage = get_default_storage()
    return _storage


def _get_retriever() -> RAGRetriever:
    global _retriever
    if _retriever is None:
        from agent.knowledge.retriever import get_default_retriever

        _retriever = get_default_retriever()
    return _retriever


def _get_ingestion() -> KnowledgeIngestion:
    global _ingestion
    if _ingestion is None:
        from agent.knowledge.ingestion import build_default_ingestion

        _ingestion = build_default_ingestion()
    return _ingestion


def reset_for_testing() -> None:
    """测试 hook：清空单例。"""
    global _storage, _retriever, _ingestion
    _storage = None
    _retriever = None
    _ingestion = None


def _resolve_stored_path(source_relpath: Any) -> str:
    """把库内相对路径（files/ 下）解析为绝对路径，仅当文件确实存在时返回（否则空串）。

    供前端点击已上传文档直接预览；零绝对路径入库红线不变（只读时拼当前数据根）。
    """
    rel = str(source_relpath or "").strip()
    if not rel:
        return ""
    try:
        fp = rag_config.kb_files_dir() / rel
        return str(fp) if fp.is_file() else ""
    except OSError:
        return ""


# 后台导入/重建任务（防 GC）+ 重建进度（内存态，供 /v1/status 轮询；重启归零不影响主流程）
_background_tasks: set[asyncio.Task[Any]] = set()
_reindex_progress: dict[str, Any] = {"indexing": False, "progress": 0.0}


# =============================================================================
# V0 外部知识库（向后兼容，不动）
# =============================================================================


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=20)


class SearchResult(BaseModel):
    doc_id: str
    title: str
    snippet: str
    score: float
    source_url: str


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    backend: str
    elapsed_ms: int


@router.get("/status")
async def knowledge_status() -> dict[str, Any]:
    """V0 mock 状态（外部 KB 连接状态）。"""
    return {"available": True, "backend": "mock"}


@router.post("/search", response_model=SearchResponse)
async def knowledge_search(body: SearchRequest) -> SearchResponse:
    """V0 外部 KB 检索（mock 占位）。"""
    ctx: KBContext = await build_kb_context(body.query, top_k=body.top_k)
    return SearchResponse(
        query=ctx.query,
        results=[
            SearchResult(
                doc_id=r.doc_id,
                title=r.title,
                snippet=r.snippet,
                score=r.score,
                source_url=r.source_url,
            )
            for r in ctx.results
        ],
        backend=ctx.backend,
        elapsed_ms=ctx.elapsed_ms,
    )


# =============================================================================
# V1 本地知识库
# =============================================================================


_ALLOWED_UPLOAD_SUFFIXES = {
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


class V1DocSummary(BaseModel):
    id: str
    title: str
    file_name: str = ""
    source_type: str
    category: str = ""
    status: str = "pending"
    error: str | None = None
    chunk_count: int = 0
    size_bytes: int = 0
    created_at: int = 0
    updated_at: int = 0
    # 版本化：逻辑资料 group + 版本号 + 是否生效 + 组内版本总数
    group_id: str = ""
    version: int = 1
    is_active: bool = True
    version_count: int = 1
    # 已复制入库的源文件绝对路径（供前端点击预览）；未复制/文件丢失时为空串
    file_path: str = ""


class V1StatusResponse(BaseModel):
    storage_available: bool
    embedding_available: bool
    reranker_available: bool = False
    stats: KnowledgeStats
    db_path: str
    embed_model: str = ""
    dim: int = 0
    needs_reindex: bool = False
    reindexing: bool = False
    reindex_progress: float = 0.0


class V1ListDocsResponse(BaseModel):
    total: int
    docs: list[V1DocSummary]


class V1UploadRequest(BaseModel):
    file_path: str = Field(..., min_length=1, max_length=4096)
    category: str = ""
    metadata: dict[str, Any] | None = None
    # 置位时：本次上传作为该文档所在逻辑资料的「新版本」（version+1，就绪后自动生效）
    base_doc_id: str | None = None


class V1UploadResponse(BaseModel):
    doc_id: str
    status: str


class V1DeleteResponse(BaseModel):
    deleted: bool


class V1VersionItem(BaseModel):
    doc_id: str
    group_id: str = ""
    version: int = 1
    status: str = "pending"
    is_active: bool = False
    chunk_count: int = 0
    size_bytes: int = 0
    file_name: str = ""
    created_at: int = 0
    updated_at: int = 0
    error: str | None = None
    file_path: str = ""


class V1ListVersionsResponse(BaseModel):
    group_id: str
    active_doc_id: str = ""
    versions: list[V1VersionItem]


class V1ActivateResponse(BaseModel):
    ok: bool
    group_id: str = ""
    active_doc_id: str = ""


class V1SetCategoryRequest(BaseModel):
    category: str = ""


class V1SetCategoryResponse(BaseModel):
    ok: bool
    group_id: str = ""
    category: str = ""


class V1SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    similarity_threshold: float = Field(default=0.0, ge=-1.0, le=1.0)
    category: str | None = None
    source_type_filter: list[str] | None = None


class V1RetrievalResult(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    source_type: str
    similarity: float
    citation: str
    content_preview: str
    source: str = ""
    page_no: int = 1
    heading_path: str = ""
    matched: list[str] = Field(default_factory=list)


class V1SearchResponse(BaseModel):
    query: str
    results: list[V1RetrievalResult]
    formatted_prompt: str
    elapsed_ms: int
    backend: str


class V1SyncResponse(BaseModel):
    synced_docs: int


class V1EntityItem(BaseModel):
    entity_name: str
    entity_type: str = ""
    parent_seq: int = 0


class V1EntitiesResponse(BaseModel):
    doc_id: str
    entities: list[V1EntityItem]
    total: int


class V1EntitySearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    top_k: int = Field(default=10, ge=1, le=50)


class V1EntitySearchResult(BaseModel):
    entity_name: str
    entity_type: str = ""
    doc_id: str = ""
    parent_seq: int = 0
    doc_title: str = ""


class V1EntitySearchResponse(BaseModel):
    query: str
    results: list[V1EntitySearchResult]
    total: int


# ---- 端点 ---------------------------------------------------------------


@router.get("/v1/status", response_model=V1StatusResponse)
async def v1_status() -> V1StatusResponse:
    """本地 KB 状态 + embedding/reranker 可达性 + 模型漂移/重建进度。"""
    storage = _get_storage()
    retriever = _get_retriever()
    emb_ok = retriever.embedding is not None
    try:
        stats = storage.get_stats()
    except Exception as e:
        logger.exception("get_stats failed")
        raise HTTPException(500, f"storage error: {e}")
    meta = storage.get_meta()
    cur_model = settings.local_embedding_model or "bge-small-zh-v1.5"
    cur_dim = int(settings.local_embedding_dim)
    try:
        from agent.knowledge.reranker import get_reranker_client

        reranker_ok = bool(settings.rag_rerank_enabled) and get_reranker_client().model_present()
    except Exception:
        reranker_ok = False
    return V1StatusResponse(
        storage_available=True,
        embedding_available=emb_ok,
        reranker_available=reranker_ok,
        stats=stats,
        db_path=storage.db_path,
        embed_model=str(meta.get("embed_model", "")),
        dim=int(meta.get("dim", 0)),
        needs_reindex=storage.needs_reindex(cur_model, cur_dim),
        reindexing=bool(_reindex_progress.get("indexing")),
        reindex_progress=float(_reindex_progress.get("progress", 0.0)),
    )


@router.get("/v1/docs", response_model=V1ListDocsResponse)
async def v1_list_docs(
    source_type: str | None = None,
    q: str | None = None,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> V1ListDocsResponse:
    storage = _get_storage()
    # 空串规约为 None（前端可能传 "" 表示不过滤）
    search_q = q.strip() if q else None
    cat = category.strip() if category else None
    docs = storage.list_docs(
        source_type=source_type,
        limit=limit,
        offset=offset,
        q=search_q,
        category=cat,
    )
    total = storage.count_docs(source_type=source_type, q=search_q, category=cat)
    return V1ListDocsResponse(
        total=total,
        docs=[
            V1DocSummary(
                id=str(d["doc_id"]),
                title=str(d.get("title", "")),
                file_name=str(d.get("file_name", "")),
                source_type=str(d.get("source_type", "")),
                category=str(d.get("category", "")),
                status=str(d.get("status", "pending")),
                error=d.get("error"),
                chunk_count=int(d.get("chunk_count", 0) or 0),
                size_bytes=int(d.get("size_bytes", 0) or 0),
                created_at=int(d.get("created_at", 0) or 0),
                updated_at=int(d.get("updated_at", 0) or 0),
                group_id=str(d.get("group_id", "") or d["doc_id"]),
                version=int(d.get("version", 1) or 1),
                is_active=bool(d.get("is_active", True)),
                version_count=int(d.get("version_count", 1) or 1),
                file_path=_resolve_stored_path(d.get("source_relpath")),
            )
            for d in docs
        ],
    )


async def _run_ingest(
    path: str,
    doc_id: str,
    category: str,
    metadata: dict[str, Any] | None,
    *,
    group_id: str | None = None,
    version: int = 1,
    is_active: bool = True,
) -> None:
    """后台导入（分块+向量化可能耗时）；ingest_file 内部已置 failed 状态。"""
    ing = _get_ingestion()
    try:
        await ing.ingest_file(
            path,
            doc_id=doc_id,
            category=category,
            metadata=metadata,
            group_id=group_id,
            version=version,
            is_active=is_active,
        )
    except IngestionError as exc:
        logger.warning("background ingest failed doc_id=%s: %s", doc_id, exc)
    except Exception:
        logger.exception("background ingest crashed doc_id=%s", doc_id)


@router.post("/v1/docs/upload", response_model=V1UploadResponse)
async def v1_upload_doc(body: V1UploadRequest) -> V1UploadResponse:
    """上传参考资料：同步预校验 + 建档，后台异步分块/向量化（前端轮询状态）。

    base_doc_id 置位时作为该文档所在逻辑资料的新版本（version+1，索引期间旧版本
    仍生效，就绪后自动切换）；否则为全新单版本资料。
    """
    import uuid

    path = Path(body.file_path)
    if not path.exists():
        raise HTTPException(404, f"file not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(400, f"unsupported file type: {suffix}")
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > int(settings.rag_max_file_mb) * 1024 * 1024:
        raise HTTPException(413, f"file too large (> {settings.rag_max_file_mb}MB)")

    storage = _get_storage()
    doc_id = uuid.uuid4().hex
    # 版本归组：base_doc_id 置位 → 加入其 group 作为下一版本，索引期间不生效（is_active=0）
    if body.base_doc_id:
        base = storage.get_doc(body.base_doc_id)
        if base is None:
            raise HTTPException(404, f"base doc not found: {body.base_doc_id}")
        group_id = str(base.get("group_id") or base["doc_id"])
        version = storage.next_version(group_id)
        is_active = False
    else:
        group_id = doc_id
        version = 1
        is_active = True

    storage.insert_doc(
        doc_id=doc_id,
        title=path.stem,
        file_name=path.name,
        source_type=suffix.lstrip("."),
        category=body.category or "",
        size_bytes=size,
        status="indexing",
        metadata=body.metadata or {},
        group_id=group_id,
        version=version,
        is_active=is_active,
    )
    task = asyncio.create_task(
        _run_ingest(
            str(path),
            doc_id,
            body.category or "",
            body.metadata,
            group_id=group_id,
            version=version,
            is_active=is_active,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return V1UploadResponse(doc_id=doc_id, status="indexing")


@router.delete("/v1/docs/{doc_id}", response_model=V1DeleteResponse)
async def v1_delete_doc(doc_id: str, all_versions: bool = False) -> V1DeleteResponse:
    """硬删除：级联清 chunks + FTS5 + 向量 + parents + 复制文件。

    all_versions=True → 删该逻辑资料的全部版本；否则只删该版本（若删的是生效版本，
    自动切换到同组最新剩余版本）。
    """
    storage = _get_storage()
    deleted = storage.hard_delete_doc(doc_id, delete_file=True, all_versions=all_versions)
    return V1DeleteResponse(deleted=deleted)


@router.get("/v1/docs/{doc_id}/versions", response_model=V1ListVersionsResponse)
async def v1_list_versions(doc_id: str) -> V1ListVersionsResponse:
    """列出该文档所属逻辑资料的全部版本（version 倒序）+ 当前生效版本。"""
    storage = _get_storage()
    doc = storage.get_doc(doc_id)
    if doc is None:
        raise HTTPException(404, f"doc not found: {doc_id}")
    group_id = str(doc.get("group_id") or doc["doc_id"])
    versions = storage.list_group_versions(group_id)
    active = next((str(v["doc_id"]) for v in versions if v.get("is_active")), "")
    return V1ListVersionsResponse(
        group_id=group_id,
        active_doc_id=active,
        versions=[
            V1VersionItem(
                doc_id=str(v["doc_id"]),
                group_id=str(v.get("group_id") or group_id),
                version=int(v.get("version", 1) or 1),
                status=str(v.get("status", "pending")),
                is_active=bool(v.get("is_active", False)),
                chunk_count=int(v.get("chunk_count", 0) or 0),
                size_bytes=int(v.get("size_bytes", 0) or 0),
                file_name=str(v.get("file_name", "")),
                created_at=int(v.get("created_at", 0) or 0),
                updated_at=int(v.get("updated_at", 0) or 0),
                error=v.get("error"),
                file_path=_resolve_stored_path(v.get("source_relpath")),
            )
            for v in versions
        ],
    )


@router.post("/v1/docs/{doc_id}/activate", response_model=V1ActivateResponse)
async def v1_activate_version(doc_id: str) -> V1ActivateResponse:
    """把指定版本设为其逻辑资料的唯一生效版本（“指定用某个旧版本”入口，O(1) 翻标志位）。"""
    storage = _get_storage()
    doc = storage.get_doc(doc_id)
    if doc is None:
        raise HTTPException(404, f"doc not found: {doc_id}")
    if not storage.activate_version(doc_id):
        raise HTTPException(409, f"activate failed: {doc_id}")
    group_id = str(doc.get("group_id") or doc["doc_id"])
    return V1ActivateResponse(ok=True, group_id=group_id, active_doc_id=doc_id)


@router.post("/v1/docs/{doc_id}/category", response_model=V1SetCategoryResponse)
async def v1_set_category(doc_id: str, body: V1SetCategoryRequest) -> V1SetCategoryResponse:
    """修改该资料（整组全部版本）的分类；同步刷新子块 category 以对齐按类检索。"""
    storage = _get_storage()
    doc = storage.get_doc(doc_id)
    if doc is None:
        raise HTTPException(404, f"doc not found: {doc_id}")
    cat = str(body.category or "")
    if not storage.set_category(doc_id, cat):
        raise HTTPException(409, f"set category failed: {doc_id}")
    group_id = str(doc.get("group_id") or doc["doc_id"])
    return V1SetCategoryResponse(ok=True, group_id=group_id, category=cat)


@router.get("/v1/docs/{doc_id}/entities", response_model=V1EntitiesResponse)
async def v1_doc_entities(doc_id: str) -> V1EntitiesResponse:
    """获取文档抽取的全部实体（按类型分组排序）。"""
    storage = _get_storage()
    doc = storage.get_doc(doc_id)
    if doc is None:
        raise HTTPException(404, f"doc not found: {doc_id}")
    ents = storage.get_entities_by_doc(doc_id)
    return V1EntitiesResponse(
        doc_id=doc_id,
        entities=[
            V1EntityItem(
                entity_name=e["entity_name"],
                entity_type=e["entity_type"],
                parent_seq=e["parent_seq"],
            )
            for e in ents
        ],
        total=len(ents),
    )


@router.post("/v1/entities/search", response_model=V1EntitySearchResponse)
async def v1_entity_search(body: V1EntitySearchRequest) -> V1EntitySearchResponse:
    """按实体名搜索，返回命中实体及其关联文档信息。"""
    storage = _get_storage()
    hits = storage.search_entities(body.query, limit=body.top_k)
    # 补充 doc_title
    doc_cache: dict[str, str] = {}
    results: list[V1EntitySearchResult] = []
    for h in hits:
        did = h["doc_id"]
        if did not in doc_cache:
            doc = storage.get_doc(did)
            doc_cache[did] = str((doc or {}).get("title", ""))
        results.append(
            V1EntitySearchResult(
                entity_name=h["entity_name"],
                entity_type=h["entity_type"],
                doc_id=did,
                parent_seq=h["parent_seq"],
                doc_title=doc_cache[did],
            )
        )
    return V1EntitySearchResponse(
        query=body.query, results=results, total=len(results)
    )


@router.post("/v1/search", response_model=V1SearchResponse)
async def v1_search(body: V1SearchRequest) -> V1SearchResponse:
    retriever = _get_retriever()
    filt: dict[str, Any] = {}
    if body.category:
        filt["category"] = body.category
    if body.source_type_filter:
        filt["source_type"] = body.source_type_filter
    ctx: RAGContext = await retriever.retrieve(
        body.query,
        top_k=body.top_k,
        similarity_threshold=body.similarity_threshold,
        filter=filt or None,
    )
    results: list[V1RetrievalResult] = []
    for r in ctx.results:
        meta = getattr(r.chunk, "metadata", {}) or {}
        results.append(
            V1RetrievalResult(
                chunk_id=r.chunk.id,
                doc_id=r.chunk.doc_id,
                doc_title=r.doc_title,
                source_type=r.source_type,
                similarity=r.similarity,
                citation=r.citation,
                content_preview=str(meta.get("child_content") or r.chunk.content)[:500],
                source=str(meta.get("source") or r.citation),
                page_no=int(meta.get("page_no", 1) or 1),
                heading_path=str(meta.get("heading_path", "") or ""),
                matched=list(meta.get("matched", []) or []),
            )
        )
    return V1SearchResponse(
        query=ctx.query,
        results=results,
        formatted_prompt=ctx.formatted_prompt,
        elapsed_ms=ctx.elapsed_ms,
        backend=ctx.backend,
    )


class V1ConfigSaveRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


@router.post("/v1/reindex")
async def v1_reindex() -> dict[str, Any]:
    """全量重建向量（模型/维度漂移自愈）；后台执行，/v1/status 轮询进度。"""
    if _reindex_progress.get("indexing"):
        return {
            "ok": True,
            "already": True,
            "progress": float(_reindex_progress.get("progress", 0.0)),
        }
    _reindex_progress["indexing"] = True
    _reindex_progress["progress"] = 0.0
    ing = _get_ingestion()

    async def _run() -> None:
        try:

            def _cb(frac: float) -> None:
                _reindex_progress["progress"] = float(frac)

            await ing.reindex(on_progress=_cb)
        except Exception:
            logger.exception("reindex failed")
        finally:
            _reindex_progress["indexing"] = False
            _reindex_progress["progress"] = 1.0

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"ok": True, "started": True}


@router.get("/v1/config")
async def v1_config_get() -> dict[str, Any]:
    """当前生效 RAG 参数 + 可编辑键 + 索引期键 + 数据位置。"""
    return {
        "config": rag_config.current_config(),
        "editable": list(rag_config.editable_keys()),
        "index_time": list(rag_config.index_time_keys()),
        "db_path": _get_storage().db_path,
        "kb_dir": str(rag_config.kb_dir()),
    }


@router.post("/v1/config")
async def v1_config_set(body: V1ConfigSaveRequest) -> dict[str, Any]:
    """保存 RAG 参数（白名单裁剪，落 kb.db）并热应用：查询期参数保存即生效，无需重启。

    重置检索/入库单例，使缓存的 top_k/reranker/embedding 按新参数惰性重建；
    索引期参数（分块/父块/上下文前缀）回传 needs_reindex 供前端提示一键重建。
    """
    res = rag_config.save_rag_config(body.config or {})
    if not res.get("ok"):
        raise HTTPException(500, str(res.get("error", "save failed")))
    # 热应用后重置单例（下一用按新 settings 重建；best-effort，不影响保存结果）
    try:
        from agent.knowledge.retriever import reset_default_retriever

        reset_default_retriever()
        reset_for_testing()  # 清 api 层 _storage/_retriever/_ingestion 缓存
    except Exception:  # pragma: no cover - 重置失败不阻断（下次重启自愈）
        logger.debug("reset singletons after config save failed", exc_info=True)
    return res


@router.post("/v1/sync/biznav", response_model=V1SyncResponse)
async def v1_sync_biznav() -> V1SyncResponse:
    ing = _get_ingestion()
    n = await ing.sync_from_biznav()
    return V1SyncResponse(synced_docs=n)


@router.post("/v1/sync/codenav", response_model=V1SyncResponse)
async def v1_sync_codenav() -> V1SyncResponse:
    ing = _get_ingestion()
    n = await ing.sync_from_codenav()
    return V1SyncResponse(synced_docs=n)
