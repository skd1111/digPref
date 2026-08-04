"""Phase 14 V0 · SQLite 持久化（image_processing_tasks 表）。

物理隔离（CLAUDE.md §6）：
    image_processing.db 与 audit / knowledge / biznav / codenav / sessions / collab /
    iam / license / log_analysis / log_index 全互不干扰。

V0 实现：
  - 单例 get_default_storage()
  - CRUD：insert_task / list_tasks / get_task / get_stats
  - WAL + foreign_keys=ON + asyncio
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent.config import settings


_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ImageProcessingStorage:
    """image_processing.db SQLite 包装。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.image_processing_db_path
        self._lock = asyncio.Lock()

    async def _connect(self) -> aiosqlite.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        await db.commit()
        return db

    async def insert_task(
        self,
        *,
        task_id: str,
        processing_type: str,
        backend: str,
        input_path: str,
        output_path: str | None,
        input_size: int,
        output_size: int,
        elapsed_ms: int,
        ok: bool,
        error: str | None,
        ocr_text: str | None,
        ocr_confidence: float | None,
        ocr_block_count: int | None,
        meta: dict[str, Any],
    ) -> int:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    """
                    INSERT INTO image_processing_tasks (
                        task_id, processing_type, backend,
                        input_path, output_path, input_size, output_size,
                        elapsed_ms, ok, error,
                        ocr_text, ocr_confidence, ocr_block_count,
                        meta_json, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id, processing_type, backend,
                        input_path, output_path, input_size, output_size,
                        elapsed_ms, 1 if ok else 0, error,
                        ocr_text, ocr_confidence, ocr_block_count,
                        json.dumps(meta, ensure_ascii=False, default=str),
                        _now_iso(),
                    ),
                )
                await db.commit()
                return cur.lastrowid or 0
            finally:
                await db.close()

    async def get_task(self, task_id: str) -> dict | None:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM image_processing_tasks WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                    (task_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                return _row_to_dict(row)
            finally:
                await db.close()

    async def list_tasks(
        self,
        *,
        processing_type: str | None = None,
        ok: bool | None = None,
        limit: int = 50,
    ) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if processing_type:
            where.append("processing_type = ?")
            params.append(processing_type)
        if ok is not None:
            where.append("ok = ?")
            params.append(1 if ok else 0)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        params.append(limit)
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    f"SELECT * FROM image_processing_tasks {where_sql} ORDER BY id DESC LIMIT ?",
                    tuple(params),
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_row_to_dict(r) for r in rows]

    async def get_stats(self) -> dict:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT processing_type, COUNT(*) as n, SUM(ok) as ok_n "
                    "FROM image_processing_tasks GROUP BY processing_type"
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        stats = {}
        for ptype, n, ok_n in rows:
            stats[ptype] = {"total": n, "ok": ok_n or 0}
        return stats


def _row_to_dict(row: tuple) -> dict:
    """SQLite 行 → dict（与列序匹配 schema.sql）。"""
    cols = [
        "id", "task_id", "processing_type", "backend",
        "input_path", "output_path", "input_size", "output_size",
        "elapsed_ms", "ok", "error",
        "ocr_text", "ocr_confidence", "ocr_block_count",
        "meta_json", "ts",
    ]
    d = dict(zip(cols, row))
    if d.get("meta_json"):
        try:
            d["meta"] = json.loads(d["meta_json"])
        except (json.JSONDecodeError, TypeError):
            d["meta"] = {}
    else:
        d["meta"] = {}
    return d


# ---- 单例工厂 ---------------------------------------------------------------

_DEFAULT_STORAGE: ImageProcessingStorage | None = None


def get_default_storage() -> ImageProcessingStorage:
    global _DEFAULT_STORAGE
    if _DEFAULT_STORAGE is None:
        _DEFAULT_STORAGE = ImageProcessingStorage()
    return _DEFAULT_STORAGE


def reset_default_storage() -> None:
    global _DEFAULT_STORAGE
    _DEFAULT_STORAGE = None