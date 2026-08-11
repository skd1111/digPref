"""ops.cases —— 专家验收工作流 Case 存储（2026-08-10）。

一次业务办理 = 一个 Case（按 project_name + feature_id 定位）。
Case 内含两类数据：
  - case_files：客户经理上传给对应专家的材料 + AI/人工验收结果
  - case_qa：   客户经理向专家的迷你问答记录

上传文件落盘在 ``{db 同级目录}/ops-cases/{case_id}/``（base64 解码写入，
不依赖宿主机路径，Docker 模式下同样可用）。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# 文件验收状态
FILE_PENDING = "pending"
FILE_REVIEWING = "reviewing"
FILE_PASSED = "passed"
FILE_REJECTED = "rejected"

ALL_FILE_STATUSES = (FILE_PENDING, FILE_REVIEWING, FILE_PASSED, FILE_REJECTED)

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})


def now() -> int:
    return int(time.time())


def slugify(value: str) -> str:
    """把任意字符串压成文件系统安全的 slug（Case 目录名用）。"""
    out = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", (value or "").strip())
    return out.strip("_") or "default"


def make_case_id(project_name: str, feature_id: str) -> str:
    return f"{slugify(project_name)}__{slugify(feature_id)}"


def _read_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


class CaseStorage:
    """Case 存储（与 business_records 共用同一个 ops sqlite 文件）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_read_schema_sql())
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """存量库补列（2026-08-10 要素提取/证据链）；CREATE IF NOT EXISTS 不覆盖旧表。"""
        for col, ddl in (
            (
                "extracted_fields",
                "ALTER TABLE case_files ADD COLUMN extracted_fields TEXT NOT NULL DEFAULT '[]'",
            ),
            ("evidence", "ALTER TABLE case_files ADD COLUMN evidence TEXT NOT NULL DEFAULT '[]'"),
            (
                "reject_marks",
                "ALTER TABLE case_files ADD COLUMN reject_marks TEXT NOT NULL DEFAULT '[]'",
            ),
            (
                "draft.last_snapshot",
                "ALTER TABLE case_drafts ADD COLUMN last_snapshot TEXT NOT NULL DEFAULT ''",
            ),
            (
                "draft.submit_count",
                "ALTER TABLE case_drafts ADD COLUMN submit_count INTEGER NOT NULL DEFAULT 0",
            ),
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    logger.warning("[ops-case] migrate %s failed: %s", col, e)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 文件目录 ----------------------------------------------------------

    def case_dir(self, case_id: str) -> Path:
        d = Path(self._db_path).parent / "ops-cases" / slugify(case_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_upload(self, case_id: str, file_name: str, content_base64: str) -> Path:
        """base64 内容落盘到 Case 目录，返回存储路径（防重名自动加后缀）。"""
        raw = base64.b64decode(content_base64)
        d = self.case_dir(case_id)
        safe_name = Path(file_name).name or "material.bin"
        target = d / safe_name
        seq = 1
        while target.exists():
            target = d / f"{Path(safe_name).stem}_{seq}{Path(safe_name).suffix}"
            seq += 1
        target.write_bytes(raw)
        return target

    # ---- case_files ---------------------------------------------------------

    def add_file(
        self,
        *,
        case_id: str,
        team_id: str,
        member_key: str,
        file_name: str,
        file_path: str,
    ) -> dict:
        ts = now()
        file_id = f"CF-{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO case_files(
                    id, case_id, team_id, member_key, file_name, file_path,
                    status, review_note, reviewed_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)
                """,
                (
                    file_id,
                    case_id,
                    team_id,
                    member_key,
                    file_name,
                    file_path,
                    FILE_PENDING,
                    ts,
                    ts,
                ),
            )
        return self.get_file(file_id)  # type: ignore[return-value]

    def get_file(self, file_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM case_files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None

    def list_files(self, case_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM case_files WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_file(
        self,
        file_id: str,
        *,
        status: str | None = None,
        review_note: str | None = None,
        reviewed_by: str | None = None,
        extracted_fields: str | None = None,
        evidence: str | None = None,
        reject_marks: str | None = None,
    ) -> dict | None:
        sets: list[str] = []
        params: list = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if review_note is not None:
            sets.append("review_note = ?")
            params.append(review_note)
        if reviewed_by is not None:
            sets.append("reviewed_by = ?")
            params.append(reviewed_by)
        if extracted_fields is not None:
            sets.append("extracted_fields = ?")
            params.append(extracted_fields)
        if evidence is not None:
            sets.append("evidence = ?")
            params.append(evidence)
        if reject_marks is not None:
            sets.append("reject_marks = ?")
            params.append(reject_marks)
        if not sets:
            return self.get_file(file_id)
        sets.append("updated_at = ?")
        params.extend([now(), file_id])
        with self._connect() as conn:
            conn.execute(f"UPDATE case_files SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_file(file_id)

    def delete_file(self, file_id: str) -> dict | None:
        row = self.get_file(file_id)
        if row is None:
            return None
        with self._connect() as conn:
            conn.execute("DELETE FROM case_files WHERE id = ?", (file_id,))
        # 落盘文件一并清理（best-effort）
        try:
            p = Path(str(row.get("file_path", "")))
            if p.exists():
                p.unlink()
        except OSError as e:
            logger.warning("[ops-case] cleanup file failed: %s", e)
        return row

    # ---- case_qa -------------------------------------------------------------

    def add_qa(self, *, case_id: str, member_key: str, question: str, answer: str) -> dict:
        qa_id = f"QA-{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"
        ts = now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO case_qa(id, case_id, member_key, question, answer, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (qa_id, case_id, member_key, question, answer, ts),
            )
        return {
            "id": qa_id,
            "case_id": case_id,
            "member_key": member_key,
            "question": question,
            "answer": answer,
            "created_at": ts,
        }

    def list_qa(self, case_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM case_qa WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_case(self, case_id: str) -> dict:
        """清空整个 Case（BUGFIX #85 重新开始办理）：删材料/问答/草稿 + 落盘目录。"""
        import shutil

        with self._connect() as conn:
            files = conn.execute(
                "DELETE FROM case_files WHERE case_id = ?", (case_id,)
            ).rowcount
            qa = conn.execute(
                "DELETE FROM case_qa WHERE case_id = ?", (case_id,)
            ).rowcount
            drafts = conn.execute(
                "DELETE FROM case_drafts WHERE case_id = ?", (case_id,)
            ).rowcount
        d = Path(self._db_path).parent / "ops-cases" / slugify(case_id)
        try:
            if d.exists():
                shutil.rmtree(d)
        except OSError as e:
            logger.warning("[ops-case] clear case dir failed: %s", e)
        return {"files": files, "qa": qa, "drafts": drafts}

    # ---- case_drafts（交付草稿：界面直填，BUGFIX #78） -----------------

    def add_draft(
        self,
        *,
        case_id: str,
        team_id: str,
        member_key: str,
        title: str,
        template_json: str,
        values_json: str = "{}",
    ) -> dict:
        draft_id = f"DR-{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"
        ts = now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO case_drafts(
                    id, case_id, team_id, member_key, title,
                    template_json, values_json, status, file_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', '', ?, ?)
                """,
                (draft_id, case_id, team_id, member_key, title, template_json, values_json, ts, ts),
            )
        return self.get_draft(draft_id)  # type: ignore[return-value]

    def get_draft(self, draft_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM case_drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row) if row else None

    def list_drafts(self, case_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM case_drafts WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_draft(
        self,
        draft_id: str,
        *,
        values_json: str | None = None,
        status: str | None = None,
        file_id: str | None = None,
        last_snapshot: str | None = None,
        bump_submit_count: bool = False,
    ) -> dict | None:
        sets: list[str] = []
        params: list = []
        if values_json is not None:
            sets.append("values_json = ?")
            params.append(values_json)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if file_id is not None:
            sets.append("file_id = ?")
            params.append(file_id)
        if last_snapshot is not None:
            sets.append("last_snapshot = ?")
            params.append(last_snapshot)
        if bump_submit_count:
            sets.append("submit_count = submit_count + 1")
        if not sets:
            return self.get_draft(draft_id)
        sets.append("updated_at = ?")
        params.extend([now(), draft_id])
        with self._connect() as conn:
            conn.execute(f"UPDATE case_drafts SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_draft(draft_id)

    # ---- case_corrections（人工纠错样本，铁律 2） -------------------------

    def add_correction(
        self,
        *,
        case_id: str,
        file_id: str,
        member_key: str,
        file_name: str,
        ai_status: str,
        ai_note: str,
        human_status: str,
        human_note: str,
    ) -> dict:
        corr_id = f"CR-{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"
        ts = now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO case_corrections(
                    id, case_id, file_id, member_key, file_name,
                    ai_status, ai_note, human_status, human_note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    corr_id,
                    case_id,
                    file_id,
                    member_key,
                    file_name,
                    ai_status,
                    ai_note,
                    human_status,
                    human_note,
                    ts,
                ),
            )
        return {
            "id": corr_id,
            "case_id": case_id,
            "file_id": file_id,
            "member_key": member_key,
            "file_name": file_name,
            "ai_status": ai_status,
            "ai_note": ai_note,
            "human_status": human_status,
            "human_note": human_note,
            "created_at": ts,
        }

    def list_corrections(self, case_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if case_id:
                rows = conn.execute(
                    "SELECT * FROM case_corrections WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM case_corrections ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # ---- case_sessions（会话管理归档映射，2026-08-11）-------------------

    def get_case_session(self, case_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM case_sessions WHERE case_id = ?", (case_id,)
            ).fetchone()
        return str(row["session_id"]) if row else None

    def set_case_session(self, case_id: str, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO case_sessions(case_id, session_id) VALUES (?, ?) "
                "ON CONFLICT(case_id) DO UPDATE SET session_id = excluded.session_id",
                (case_id, session_id),
            )


def is_image_file(file_name: str) -> bool:
    return Path(file_name).suffix.lower() in _IMAGE_SUFFIXES


def dump_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)
