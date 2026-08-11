"""ops.storage —— business_records SQLite 存储（Phase 2H）。"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .models import BusinessRecord

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now() -> int:
    return int(time.time())


def _read_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


class BusinessRecordStorage:
    """业务记录存储（单文件 sqlite，形态对齐 biznav/storage.py）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_read_schema_sql())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _next_seq(self) -> str:
        """当日三位递增编号：OPR-YYYYMMDD-NNN。"""
        date_str = time.strftime("%Y%m%d")
        id_prefix = f"OPR-{date_str}-"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM business_records WHERE id LIKE ?",
                (f"{id_prefix}%",),
            ).fetchall()
        max_seq = 0
        for r in rows:
            try:
                max_seq = max(max_seq, int(str(r["id"]).rsplit("-", 1)[1]))
            except (ValueError, IndexError):
                continue
        return f"{id_prefix}{max_seq + 1:03d}"

    def create(
        self,
        *,
        project_name: str,
        feature_id: str,
        business_type: str = "",
        title: str = "",
        summary: str = "",
        materials_checked: list[str] | None = None,
        materials_missing: list[str] | None = None,
        risk_points: list[str] | None = None,
        result: str = "done",
        skill_id: str = "",
        session_id: str = "",
        source: str = "ai",
        created_by: str = "",
    ) -> BusinessRecord:
        ts = now()
        rec = BusinessRecord(
            id=self._next_seq(),
            project_name=project_name,
            feature_id=feature_id,
            business_type=business_type,
            title=title,
            summary=summary,
            materials_checked=list(materials_checked or []),
            materials_missing=list(materials_missing or []),
            risk_points=list(risk_points or []),
            result=result,
            skill_id=skill_id,
            session_id=session_id,
            source=source,
            created_by=created_by,
            created_at=ts,
            updated_at=ts,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO business_records(
                    id, project_name, feature_id, business_type, title, summary,
                    materials_checked, materials_missing, risk_points, result,
                    skill_id, session_id, source, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.id,
                    rec.project_name,
                    rec.feature_id,
                    rec.business_type,
                    rec.title,
                    rec.summary,
                    json.dumps(rec.materials_checked, ensure_ascii=False),
                    json.dumps(rec.materials_missing, ensure_ascii=False),
                    json.dumps(rec.risk_points, ensure_ascii=False),
                    rec.result,
                    rec.skill_id,
                    rec.session_id,
                    rec.source,
                    rec.created_by,
                    rec.created_at,
                    rec.updated_at,
                ),
            )
        return rec

    def get(self, record_id: str) -> BusinessRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM business_records WHERE id = ?", (record_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list(
        self,
        *,
        feature_id: str | None = None,
        project_name: str | None = None,
        limit: int = 100,
    ) -> list[BusinessRecord]:
        sql = "SELECT * FROM business_records WHERE 1=1"
        params: list = []
        if feature_id:
            sql += " AND feature_id = ?"
            params.append(feature_id)
        if project_name:
            sql += " AND project_name = ?"
            params.append(project_name)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def delete(self, record_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM business_records WHERE id = ?", (record_id,))
        return cur.rowcount > 0

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> BusinessRecord:
        d = dict(row)
        for key in ("materials_checked", "materials_missing", "risk_points"):
            try:
                d[key] = json.loads(d.get(key) or "[]")
            except (ValueError, TypeError):
                d[key] = []
        return BusinessRecord.from_dict(d)
