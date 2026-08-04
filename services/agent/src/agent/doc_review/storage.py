"""doc_review.db SQLite 存储（aiosqlite，模式对齐 audit_expert.store）。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent.config import settings
from agent.doc_review.models import ParsedDocument

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocReviewStorage:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.doc_review_db_path
        self._lock = asyncio.Lock()

    async def _connect(self) -> aiosqlite.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        await db.commit()
        return db

    # ---- documents ----

    async def insert_document(self, doc: ParsedDocument) -> None:
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    """INSERT INTO documents (doc_id, file_name, file_path, format, page_count, pages_json, full_text, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        doc.doc_id,
                        doc.file_name,
                        doc.file_path,
                        doc.format.value,
                        doc.page_count,
                        json.dumps([p.model_dump() for p in doc.pages], ensure_ascii=False),
                        doc.full_text,
                        _now_iso(),
                    ),
                )
                await db.commit()
            finally:
                await db.close()

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,))
                row = await cur.fetchone()
                return self._doc_row(row) if row else None
            finally:
                await db.close()

    async def list_documents(self) -> list[dict[str, Any]]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute("SELECT * FROM documents ORDER BY id DESC")
                rows = await cur.fetchall()
                out = []
                for row in rows:
                    item = self._doc_row(row)
                    cur2 = await db.execute(
                        "SELECT status, overall_risk_level FROM analysis_runs "
                        "WHERE doc_id = ? ORDER BY id DESC LIMIT 1",
                        (item["doc_id"],),
                    )
                    r2 = await cur2.fetchone()
                    item["status"] = r2[0] if r2 else "none"
                    item["overall_risk_level"] = r2[1] if r2 else None
                    out.append(item)
                return out
            finally:
                await db.close()

    async def delete_document(self, doc_id: str) -> bool:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute("SELECT 1 FROM documents WHERE doc_id = ?", (doc_id,))
                if await cur.fetchone() is None:
                    return False
                await db.execute("DELETE FROM findings WHERE doc_id = ?", (doc_id,))
                await db.execute("DELETE FROM analysis_runs WHERE doc_id = ?", (doc_id,))
                await db.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
                await db.commit()
                return True
            finally:
                await db.close()

    @staticmethod
    def _doc_row(row: Any) -> dict[str, Any]:
        cols = [
            "id",
            "doc_id",
            "file_name",
            "file_path",
            "format",
            "page_count",
            "pages_json",
            "full_text",
            "created_at",
        ]
        item = dict(zip(cols, row, strict=False))
        item["pages"] = json.loads(item.pop("pages_json"))
        return item

    # ---- analysis_runs ----

    async def insert_run(self, *, run_id: str, doc_id: str, status: str) -> None:
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    """INSERT INTO analysis_runs (run_id, doc_id, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (run_id, doc_id, status, _now_iso(), _now_iso()),
                )
                await db.commit()
            finally:
                await db.close()

    async def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        doc_category: str | None = None,
        risk_types: list[str] | None = None,
        overall_risk_level: str | None = None,
        summary: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        error: str | None = None,
    ) -> None:
        async with self._lock:
            db = await self._connect()
            try:
                sets: list[str] = ["updated_at = ?"]
                values: list[Any] = [_now_iso()]
                if status is not None:
                    sets.append("status = ?")
                    values.append(status)
                if doc_category is not None:
                    sets.append("doc_category = ?")
                    values.append(doc_category)
                if risk_types is not None:
                    sets.append("risk_types_json = ?")
                    values.append(json.dumps(risk_types, ensure_ascii=False))
                if overall_risk_level is not None:
                    sets.append("overall_risk_level = ?")
                    values.append(overall_risk_level)
                if summary is not None:
                    sets.append("summary = ?")
                    values.append(summary)
                if model_provider is not None:
                    sets.append("model_provider = ?")
                    values.append(model_provider)
                if model_name is not None:
                    sets.append("model_name = ?")
                    values.append(model_name)
                if error is not None:
                    sets.append("error = ?")
                    values.append(error)
                values.append(run_id)
                await db.execute(
                    f"UPDATE analysis_runs SET {', '.join(sets)} WHERE run_id = ?",
                    values,
                )
                await db.commit()
            finally:
                await db.close()

    async def latest_run(self, doc_id: str) -> dict[str, Any] | None:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM analysis_runs WHERE doc_id = ? ORDER BY id DESC LIMIT 1",
                    (doc_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [
                    "id",
                    "run_id",
                    "doc_id",
                    "status",
                    "doc_category",
                    "risk_types_json",
                    "overall_risk_level",
                    "summary",
                    "model_provider",
                    "model_name",
                    "error",
                    "created_at",
                    "updated_at",
                ]
                item = dict(zip(cols, row, strict=False))
                item["risk_types"] = (
                    json.loads(item["risk_types_json"]) if item["risk_types_json"] else []
                )
                item.pop("risk_types_json", None)
                return item
            finally:
                await db.close()

    # ---- findings ----

    async def insert_findings(
        self, run_id: str, doc_id: str, findings: list[dict[str, Any]]
    ) -> None:
        async with self._lock:
            db = await self._connect()
            try:
                for f in findings:
                    await db.execute(
                        """INSERT INTO findings (finding_id, run_id, doc_id, risk_type, risk_level, title,
                           description, suggestion, rule_ref, evidence_text, positions_json, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            f["finding_id"],
                            run_id,
                            doc_id,
                            f["risk_type"],
                            f["risk_level"],
                            f["title"],
                            f.get("description", ""),
                            f.get("suggestion", ""),
                            f.get("rule_ref"),
                            f.get("evidence_text", ""),
                            f.get("positions_json", "[]"),
                            _now_iso(),
                        ),
                    )
                await db.commit()
            finally:
                await db.close()

    async def list_findings(self, doc_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            db = await self._connect()
            try:
                if run_id:
                    cur = await db.execute(
                        "SELECT * FROM findings WHERE doc_id = ? AND run_id = ? ORDER BY id ASC",
                        (doc_id, run_id),
                    )
                else:
                    cur = await db.execute(
                        "SELECT * FROM findings WHERE doc_id = ? ORDER BY id ASC",
                        (doc_id,),
                    )
                rows = await cur.fetchall()
                cols = [
                    "id",
                    "finding_id",
                    "run_id",
                    "doc_id",
                    "risk_type",
                    "risk_level",
                    "title",
                    "description",
                    "suggestion",
                    "rule_ref",
                    "evidence_text",
                    "positions_json",
                    "created_at",
                ]
                out = []
                for row in rows:
                    item = dict(zip(cols, row, strict=False))
                    item["positions"] = json.loads(item.pop("positions_json"))
                    out.append(item)
                return out
            finally:
                await db.close()


_default_storage: DocReviewStorage | None = None


def get_default_storage() -> DocReviewStorage:
    global _default_storage
    if _default_storage is None:
        _default_storage = DocReviewStorage()
    return _default_storage


def reset_default_storage() -> None:
    global _default_storage
    _default_storage = None
