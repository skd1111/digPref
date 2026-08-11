"""Phase 7 V0 · 数据专家 SQLite 存储 —— 3 表 CRUD + Parquet 结果落盘/读取。

物理隔离：data_expert.db 与 audit / router / knowledge / ssh / audit_expert 等独立。
结果集大对象 → 本地 Parquet 文件（result_data_ref 存路径），不塞进 SQLite。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite

from agent.config import settings

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class DataExpertStorage:
    """data_expert.db 包装。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.data_expert_db_path
        self._lock = asyncio.Lock()

    async def _connect(self) -> aiosqlite.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        await db.commit()
        return db

    # ---- data_sources ----------------------------------------------------

    async def upsert_source(
        self,
        *,
        source_id: str,
        name: str,
        source_type: str,
        connection_ref: str = "",
        schema_cache: list[dict] | None = None,
        updated_at: int = 0,
    ) -> None:
        cache_json = json.dumps(schema_cache or [], ensure_ascii=False, default=str)
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    """
                    INSERT INTO data_sources (id, name, type, connection_ref, schema_cache, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, type=excluded.type,
                        connection_ref=excluded.connection_ref,
                        schema_cache=excluded.schema_cache,
                        updated_at=excluded.updated_at
                    """,
                    (source_id, name, source_type, connection_ref, cache_json, updated_at),
                )
                await db.commit()
            finally:
                await db.close()

    async def list_sources(self) -> list[dict]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute("SELECT * FROM data_sources ORDER BY updated_at DESC")
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_source_row_to_dict(r) for r in rows]

    async def get_source(self, source_id: str) -> dict | None:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute("SELECT * FROM data_sources WHERE id = ?", (source_id,))
                row = await cur.fetchone()
            finally:
                await db.close()
        return _source_row_to_dict(row) if row else None

    # ---- analysis_tasks --------------------------------------------------

    async def insert_task(
        self,
        *,
        task_id: str,
        name: str,
        user_id: str,
        query_sql: str = "",
        python_script: str = "",
        result_metadata: dict | None = None,
        result_data_ref: str = "",
        chart_config: dict | None = None,
        created_at: int = 0,
    ) -> None:
        meta_json = json.dumps(result_metadata or {}, ensure_ascii=False, default=str)
        chart_json = json.dumps(chart_config or {}, ensure_ascii=False, default=str)
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    """
                    INSERT INTO analysis_tasks
                        (id, name, user_id, query_sql, python_script,
                         result_metadata, result_data_ref, chart_config, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        name,
                        user_id,
                        query_sql,
                        python_script,
                        meta_json,
                        result_data_ref,
                        chart_json,
                        created_at,
                    ),
                )
                await db.commit()
            finally:
                await db.close()

    async def list_tasks(self, user_id: str | None = None, limit: int = 50) -> list[dict]:
        where = "WHERE user_id = ?" if user_id else ""
        params: list[Any] = [user_id, limit] if user_id else [limit]
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    f"SELECT * FROM analysis_tasks {where} ORDER BY created_at DESC LIMIT ?",
                    tuple(params),
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_task_row_to_dict(r) for r in rows]

    async def get_task(self, task_id: str) -> dict | None:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute("SELECT * FROM analysis_tasks WHERE id = ?", (task_id,))
                row = await cur.fetchone()
            finally:
                await db.close()
        return _task_row_to_dict(row) if row else None

    # ---- report_templates ------------------------------------------------

    async def upsert_template(
        self,
        *,
        template_id: str,
        name: str,
        description: str = "",
        task_id: str,
        schedule_cron: str = "",
        export_format: str = "excel",
        created_by: str = "",
        is_public: bool = False,
    ) -> None:
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    """
                    INSERT INTO report_templates
                        (id, name, description, task_id, schedule_cron,
                         export_format, created_by, is_public)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, description=excluded.description,
                        task_id=excluded.task_id, schedule_cron=excluded.schedule_cron,
                        export_format=excluded.export_format,
                        created_by=excluded.created_by, is_public=excluded.is_public
                    """,
                    (
                        template_id,
                        name,
                        description,
                        task_id,
                        schedule_cron,
                        export_format,
                        created_by,
                        1 if is_public else 0,
                    ),
                )
                await db.commit()
            finally:
                await db.close()

    async def list_templates(self) -> list[dict]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute("SELECT * FROM report_templates ORDER BY name")
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_template_row_to_dict(r) for r in rows]

    async def list_templates_with_cron(self) -> list[dict]:
        """带定时计划的报表模板（供 ReportScheduler，缺口 7）。"""
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM report_templates WHERE schedule_cron != '' ORDER BY name"
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_template_row_to_dict(r) for r in rows]


# ---- Parquet 结果落盘 / 读取 -------------------------------------------------


def save_result_parquet(df: Any, task_id: str) -> str:
    """将 DataFrame 落盘为 Parquet，返回文件路径。"""
    result_dir = Path(settings.data_result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    path = result_dir / f"{task_id}.parquet"
    df.to_parquet(str(path), index=False)
    return str(path)


def load_result_parquet(path: str) -> Any:
    """从 Parquet 文件读取 DataFrame。"""
    import pandas as pd

    return pd.read_parquet(path)


# ---- 行 → dict helpers -------------------------------------------------------


def _source_row_to_dict(row: tuple) -> dict:
    cols = ["id", "name", "type", "connection_ref", "schema_cache", "updated_at"]
    d = dict(zip(cols, row))
    if d.get("schema_cache"):
        try:
            d["schema_cache"] = json.loads(d["schema_cache"])
        except (json.JSONDecodeError, TypeError):
            d["schema_cache"] = []
    return d


def _task_row_to_dict(row: tuple) -> dict:
    cols = [
        "id",
        "name",
        "user_id",
        "query_sql",
        "python_script",
        "result_metadata",
        "result_data_ref",
        "chart_config",
        "created_at",
    ]
    d = dict(zip(cols, row))
    for key in ("result_metadata", "chart_config"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = {}
    return d


def _template_row_to_dict(row: tuple) -> dict:
    cols = [
        "id",
        "name",
        "description",
        "task_id",
        "schedule_cron",
        "export_format",
        "created_by",
        "is_public",
    ]
    return dict(zip(cols, row))


# ---- 单例工厂 -----------------------------------------------------------------

_DEFAULT_STORAGE: DataExpertStorage | None = None


def get_default_storage() -> DataExpertStorage:
    global _DEFAULT_STORAGE
    if _DEFAULT_STORAGE is None:
        _DEFAULT_STORAGE = DataExpertStorage()
    return _DEFAULT_STORAGE


def reset_default_storage() -> None:
    global _DEFAULT_STORAGE
    _DEFAULT_STORAGE = None
