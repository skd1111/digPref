"""Phase 15 V0 · preview_sessions 表持久化（preview.db 物理隔离）。

与 audit / router / knowledge / ssh 等 14 个 db 全互不干扰。
WAL + asyncio + 单例。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite

from agent.config import settings
from agent.preview.models import PreviewSession

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class PreviewStorage:
    """preview.db SQLite 包装。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.preview_db_path
        self._lock = asyncio.Lock()

    async def _connect(self) -> aiosqlite.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        await db.commit()
        return db

    async def upsert_session(self, session: PreviewSession) -> None:
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    """
                    INSERT INTO preview_sessions (
                        id, project_path, entry_file, framework, port, url,
                        status, created_at, last_active_at, config_path,
                        install_progress
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        last_active_at = excluded.last_active_at,
                        config_path = excluded.config_path,
                        install_progress = excluded.install_progress
                    """,
                    (
                        session.id,
                        session.project_path,
                        session.entry_file,
                        session.framework.value,
                        session.port,
                        session.url,
                        session.status.value,
                        session.created_at,
                        session.last_active_at,
                        session.config_path,
                        session.install_progress,
                    ),
                )
                await db.commit()
            finally:
                await db.close()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM preview_sessions WHERE id = ?",
                    (session_id,),
                )
                row = await cur.fetchone()
                return _row_to_dict(row) if row else None
            finally:
                await db.close()

    async def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM preview_sessions ORDER BY created_at DESC LIMIT ?",
                    (min(limit, 500),),
                )
                rows = await cur.fetchall()
                return [_row_to_dict(r) for r in rows]
            finally:
                await db.close()

    async def list_active_sessions(self) -> list[dict[str, Any]]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM preview_sessions "
                    "WHERE status IN ('starting', 'running', 'installing') "
                    "ORDER BY created_at DESC",
                )
                rows = await cur.fetchall()
                return [_row_to_dict(r) for r in rows]
            finally:
                await db.close()

    async def delete_session(self, session_id: str) -> bool:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "DELETE FROM preview_sessions WHERE id = ?",
                    (session_id,),
                )
                await db.commit()
                return cur.rowcount > 0
            finally:
                await db.close()


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    """SQLite 行 → dict（与 schema.sql 列序匹配）。"""
    cols = [
        "id",
        "project_path",
        "entry_file",
        "framework",
        "port",
        "url",
        "status",
        "created_at",
        "last_active_at",
        "config_path",
        "install_progress",
    ]
    return dict(zip(cols, row, strict=False))


_default_storage: PreviewStorage | None = None


def get_default_storage() -> PreviewStorage:
    global _default_storage
    if _default_storage is None:
        _default_storage = PreviewStorage()
    return _default_storage


def reset_default_storage() -> None:
    global _default_storage
    _default_storage = None
