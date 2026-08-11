"""knowledge.api —— Phase 4 知识库 FastAPI 路由（V0 外部 + V1 本地）。

路由分组：
- /knowledge/status                V0 mock 状态（向后兼容）
- /knowledge/search                V0 外部 KB 检索（向后兼容）
- /knowledge/v1/status             V1 本地 KB 状态（文档/块/搜索统计）
- /knowledge/v1/docs               V1 文档列表 + 上传 + 删除
- /knowledge/v1/search             V1 RAG 检索（向量 + LIKE 兜底）
- /knowledge/v1/sync/biznav        V1 从 Phase 2G 同步业务规则
- /knowledge/v1/sync/codenav       V1 从 Phase 2F 同步代码符号（实验性）

V0/V1 共存：V0 路由不动；新功能全部走 /v1 前缀。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
async def knowledge_status():
    """V0 mock 状态（外部 KB 连接状态）。"""
    return {"available": True, "backend": "mock"}


@router.post("/search", response_model=SearchResponse)
async def knowledge_search(body: SearchRequest):
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


class V1DocSummary(BaseModel):
    id: str
    title: str
    source_type: str
    source_path: str | None = None
    chunk_count: int
    created_at: int
    updated_at: int


class V1StatusResponse(BaseModel):
    storage_available: bool
    embedding_available: bool
    stats: KnowledgeStats
    db_path: str


class V1ListDocsResponse(BaseModel):
    total: int
    docs: list[V1DocSummary]


class V1UploadRequest(BaseModel):
    file_path: str = Field(..., min_length=1, max_length=4096)
    metadata: dict[str, Any] | None = None


class V1UploadResponse(BaseModel):
    doc: V1DocSummary
    chunks: int


class V1DeleteResponse(BaseModel):
    deleted: bool


class V1SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=20)
    similarity_threshold: float = Field(default=0.0, ge=-1.0, le=1.0)
    source_type_filter: list[str] | None = None


class V1RetrievalResult(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    source_type: str
    similarity: float
    citation: str
    content_preview: str


class V1SearchResponse(BaseModel):
    query: str
    results: list[V1RetrievalResult]
    formatted_prompt: str
    elapsed_ms: int
    backend: str


class V1SyncResponse(BaseModel):
    synced_docs: int


# ---- 端点 ---------------------------------------------------------------


@router.get("/v1/status", response_model=V1StatusResponse)
async def v1_status():
    """本地 KB 状态 + embedding 客户端可达性。"""
    storage = _get_storage()
    retriever = _get_retriever()
    emb_ok = retriever.embedding is not None
    try:
        stats = storage.get_stats()
    except Exception as e:
        logger.exception("get_stats failed")
        raise HTTPException(500, f"storage error: {e}")
    return V1StatusResponse(
        storage_available=True,
        embedding_available=emb_ok,
        stats=stats,
        db_path=storage.db_path,
    )


@router.get("/v1/docs", response_model=V1ListDocsResponse)
async def v1_list_docs(
    source_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    storage = _get_storage()
    docs = storage.list_docs(source_type=source_type, limit=limit, offset=offset)
    total = storage.count_docs(source_type=source_type)
    return V1ListDocsResponse(
        total=total,
        docs=[
            V1DocSummary(
                id=d.id,
                title=d.title,
                source_type=d.source_type,
                source_path=d.source_path,
                chunk_count=d.chunk_count,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
            for d in docs
        ],
    )


@router.post("/v1/docs/upload", response_model=V1UploadResponse)
async def v1_upload_doc(body: V1UploadRequest):
    """按文件后缀自动路由：.md / .yaml → markdown；.json → swagger；.pdf → pdf。"""
    path = Path(body.file_path)
    ing = _get_ingestion()
    try:
        if path.suffix.lower() in (".md", ".markdown", ".yaml", ".yml"):
            doc = await ing.ingest_markdown_file(path, metadata=body.metadata)
        elif path.suffix.lower() == ".json":
            doc = await ing.ingest_swagger_file(path, metadata=body.metadata)
        elif path.suffix.lower() == ".pdf":
            doc = await ing.ingest_pdf_file(path, metadata=body.metadata)
        else:
            raise HTTPException(400, f"unsupported file type: {path.suffix}")
    except IngestionError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return V1UploadResponse(
        doc=V1DocSummary(
            id=doc.id,
            title=doc.title,
            source_type=doc.source_type,
            source_path=doc.source_path,
            chunk_count=doc.chunk_count,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        ),
        chunks=doc.chunk_count,
    )


@router.delete("/v1/docs/{doc_id}", response_model=V1DeleteResponse)
async def v1_delete_doc(doc_id: str):
    storage = _get_storage()
    deleted = storage.soft_delete_doc(doc_id)
    return V1DeleteResponse(deleted=deleted)


@router.post("/v1/search", response_model=V1SearchResponse)
async def v1_search(body: V1SearchRequest):
    retriever = _get_retriever()
    ctx: RAGContext = await retriever.retrieve(
        body.query,
        top_k=body.top_k,
        similarity_threshold=body.similarity_threshold,
        source_type_filter=body.source_type_filter,
    )
    return V1SearchResponse(
        query=ctx.query,
        results=[
            V1RetrievalResult(
                chunk_id=r.chunk.id,
                doc_id=r.chunk.doc_id,
                doc_title=r.doc_title,
                source_type=r.source_type,
                similarity=r.similarity,
                citation=r.citation,
                content_preview=r.chunk.content[:500],
            )
            for r in ctx.results
        ],
        formatted_prompt=ctx.formatted_prompt,
        elapsed_ms=ctx.elapsed_ms,
        backend=ctx.backend,
    )


@router.post("/v1/sync/biznav", response_model=V1SyncResponse)
async def v1_sync_biznav():
    ing = _get_ingestion()
    n = await ing.sync_from_biznav()
    return V1SyncResponse(synced_docs=n)


@router.post("/v1/sync/codenav", response_model=V1SyncResponse)
async def v1_sync_codenav():
    ing = _get_ingestion()
    n = await ing.sync_from_codenav()
    return V1SyncResponse(synced_docs=n)
