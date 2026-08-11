"""knowledge.storage —— Phase 4 V1 知识库 SQLite DAO（占位恢复版）。

仅保留最小接口定义以满足其他模块 import 依赖。完整实现（CRUD + 向量检索）
随 Phase 4 V1 实际推进补全。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class KnowledgeStorage:
    """知识库 SQLite DAO（占位实现 —— 实际查询返空）。"""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        logger.debug("[KB storage] initialized (stub) at %s", self.db_path)

    def upsert_doc(self, doc):  # type: ignore[no-untyped-def]
        return doc

    def get_doc(self, doc_id: str):
        return None

    def list_docs(self, source_type=None, limit=100, offset=0):
        return []

    def count_docs(self, source_type=None) -> int:
        return 0

    def soft_delete_doc(self, doc_id: str) -> bool:
        return False

    def hard_delete_doc(self, doc_id: str) -> bool:
        return False

    def upsert_chunks(self, doc_id: str, chunks) -> None:  # type: ignore[no-untyped-def]
        return None

    def get_chunks_by_doc(self, doc_id: str):
        return []

    def get_chunk(self, chunk_id: str):
        return None

    def search_by_vector(
        self, query_embedding, top_k=3, similarity_threshold=0.0, source_type_filter=None
    ):
        return []

    def search_by_text(self, query: str, limit=10, source_type_filter=None):
        return []

    def log_search(
        self, query, results_count, avg_similarity, latency_ms, user_id=None, top_k=3
    ) -> None:
        return None

    def get_stats(self):  # type: ignore[no-untyped-def]
        from agent.knowledge.models import KnowledgeStats

        return KnowledgeStats()

    def list_source_types(self) -> list[str]:
        return []


_default_storage: KnowledgeStorage | None = None


def get_default_storage() -> KnowledgeStorage:
    global _default_storage
    if _default_storage is None:
        from pathlib import Path

        from agent.config import settings

        db_path = getattr(settings, "knowledge_db_path", None) or str(
            Path.home() / ".eaide" / "knowledge.db"
        )
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _default_storage = KnowledgeStorage(db_path)
    return _default_storage


def reset_default_storage() -> None:
    global _default_storage
    _default_storage = None


def encode_embedding(vec) -> bytes:
    import struct

    return struct.pack(f"<{len(vec)}f", *vec)


def decode_embedding(blob: bytes, dim: int):
    import struct

    n = len(blob) // 4
    out = struct.unpack(f"<{min(n, dim)}f", blob[: min(n, dim) * 4])
    return list(out)
