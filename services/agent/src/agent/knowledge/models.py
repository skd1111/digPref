"""knowledge.models —— Phase 4 V1 知识库数据类（占位恢复版）。

仅保留 models.py 最小定义以满足其他模块的 import 依赖。
详细实现（dataclass 字段、JSON helper）随 Phase 4 V1 实际推进补全。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ---- Source 类型常量 -------------------------------------------------------

SOURCE_MARKDOWN = "markdown"
SOURCE_SWAGGER = "swagger"
SOURCE_CONVERSATION = "conversation"
SOURCE_BUSINESS_RULE = "business_rule"
SOURCE_CODE_SYMBOL = "code_symbol"
SOURCE_PDF = "pdf"

ALL_SOURCE_TYPES: tuple[str, ...] = (
    SOURCE_MARKDOWN, SOURCE_SWAGGER, SOURCE_CONVERSATION,
    SOURCE_BUSINESS_RULE, SOURCE_CODE_SYMBOL, SOURCE_PDF,
)


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeDoc:
    id: str
    title: str
    source_type: str
    source_path: Optional[str] = None
    chunk_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = 0
    updated_at: int = 0
    deleted_at: Optional[int] = None

    @classmethod
    def new(cls, *, title: str, source_type: str, source_path: str | None = None,
            metadata: dict | None = None) -> "KnowledgeDoc":
        now = int(time.time())
        return cls(
            id=str(uuid.uuid4()),
            title=title,
            source_type=source_type,
            source_path=source_path,
            chunk_count=0,
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "source_type": self.source_type,
            "source_path": self.source_path, "chunk_count": self.chunk_count,
            "metadata": self.metadata, "created_at": self.created_at,
            "updated_at": self.updated_at, "deleted_at": self.deleted_at,
        }


@dataclass
class KnowledgeChunk:
    id: str
    doc_id: str
    seq: int
    content: str
    token_count: int = 0
    embedding: Optional[list[float]] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = 0


@dataclass
class RetrievalResult:
    chunk: "KnowledgeChunk"
    similarity: float
    doc_title: str
    source_type: str
    citation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk": self.chunk.to_dict() if self.chunk else None,
            "similarity": self.similarity,
            "doc_title": self.doc_title,
            "source_type": self.source_type,
            "citation": self.citation,
        }


@dataclass
class RAGContext:
    query: str
    results: list[RetrievalResult] = field(default_factory=list)
    formatted_prompt: str = ""
    elapsed_ms: int = 0
    backend: str = "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "formatted_prompt": self.formatted_prompt,
            "elapsed_ms": self.elapsed_ms,
            "backend": self.backend,
        }


@dataclass
class KnowledgeStats:
    total_docs: int = 0
    total_chunks: int = 0
    by_source_type: dict[str, int] = field(default_factory=dict)
    search_total: int = 0
    avg_similarity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_docs": self.total_docs,
            "total_chunks": self.total_chunks,
            "by_source_type": self.by_source_type,
            "search_total": self.search_total,
            "avg_similarity": self.avg_similarity,
        }