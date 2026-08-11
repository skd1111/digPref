"""knowledge.ingestion —— Phase 4 V1 知识库导入编排（占位恢复版）。

仅保留最小接口定义以满足其他模块 import 依赖。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    pass


class KnowledgeIngestion:
    """导入编排器（占位实现）。"""

    def __init__(self, storage, embedding=None):
        self.storage = storage
        self.embedding = embedding

    async def ingest_markdown_file(self, path, *, metadata=None):
        raise IngestionError("not implemented")

    async def ingest_swagger_file(self, path, *, metadata=None):
        raise IngestionError("not implemented")

    async def ingest_pdf_file(self, path, *, metadata=None):
        raise IngestionError("not implemented")

    async def sync_from_biznav(self) -> int:
        return 0

    async def sync_from_codenav(self) -> int:
        return 0


def build_default_ingestion() -> KnowledgeIngestion:
    from agent.knowledge.retriever import build_default_embedding_client
    from agent.knowledge.storage import get_default_storage

    storage = get_default_storage()
    embedding = build_default_embedding_client()
    return KnowledgeIngestion(storage, embedding)
