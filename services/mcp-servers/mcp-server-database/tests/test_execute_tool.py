"""Tests for the execute tool — focuses on HITL gate, transaction, audit."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mcp_server_database.tools import execute
from mcp_server_database.tools.execute import ApprovalMissingError, ExecuteError


@pytest.fixture
def sqlite_db(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO users VALUES (1, 'alice'), (2, 'bob');
    """)
    conn.close()
    return db


def _seed_audit_approval(tmp_path: Path, approval_id: str) -> None:
    """Write an approval row into the shared audit SQLite so execute.run can verify it."""
    db = tmp_path / "audit.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT, payload TEXT, ts TEXT,
            operator TEXT, run_id TEXT
        );
    """)
    conn.execute(
        "INSERT INTO audit(action, payload, ts) VALUES (?, ?, ?)",
        (
            "approval.decision",
            json.dumps({"approval_id": approval_id, "decision": "approve"}),
            # 审批有 5 分钟有效期，必须用当前时间
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        ),
    )
    conn.commit()
    conn.close()


class TestApprovalGate:
    def test_missing_approval_id(self, monkeypatch):
        monkeypatch.delenv("EAIDE_APPROVAL_DRY_RUN", raising=False)
        with pytest.raises(ApprovalMissingError):
            asyncio.run(
                execute.run({"connection": "x", "sql": "UPDATE users SET name='x' WHERE id=1"})
            )

    def test_unverified_approval_rejected(self, tmp_path, monkeypatch):
        monkeypatch.delenv("EAIDE_APPROVAL_DRY_RUN", raising=False)
        monkeypatch.setenv("EAIDE_AUDIT_DB", str(tmp_path / "audit.sqlite"))
        # No approval row seeded
        with pytest.raises(ApprovalMissingError, match="not found"):
            asyncio.run(
                execute.run(
                    {
                        "connection": "x",
                        "sql": "UPDATE users SET name='x' WHERE id=1",
                        "approval_id": "ghost",
                    }
                )
            )

    def test_verified_approval_proceeds(self, tmp_path, monkeypatch, sqlite_db):
        monkeypatch.delenv("EAIDE_APPROVAL_DRY_RUN", raising=False)
        monkeypatch.setenv("EAIDE_AUDIT_DB", str(tmp_path / "audit.sqlite"))
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        _seed_audit_approval(tmp_path, "appr_123")
        out = asyncio.run(
            execute.run(
                {
                    "connection": "test_sq",
                    "sql": "UPDATE users SET name = 'eve' WHERE id = 1",
                    "approval_id": "appr_123",
                }
            )
        )
        assert out["ok"] is True
        assert out["rows_affected"] == 1
        # DB really updated
        conn = sqlite3.connect(sqlite_db)
        row = conn.execute("SELECT name FROM users WHERE id = 1").fetchone()
        conn.close()
        assert row[0] == "eve"


class TestSafetyRejection:
    def test_drop_rejected_even_with_approval(self, tmp_path, monkeypatch, sqlite_db):
        monkeypatch.delenv("EAIDE_APPROVAL_DRY_RUN", raising=False)
        monkeypatch.setenv("EAIDE_AUDIT_DB", str(tmp_path / "audit.sqlite"))
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        _seed_audit_approval(tmp_path, "appr_456")
        # DROP is caught by the sqlglot validator first ("statement type not
        # allowed: Drop") — that's the correct rejection path, even more
        # specific than dangerous_ops.
        with pytest.raises(ExecuteError, match=r"rejected by safety policy"):
            asyncio.run(
                execute.run(
                    {
                        "connection": "test_sq",
                        "sql": "DROP TABLE users",
                        "approval_id": "appr_456",
                    }
                )
            )

    def test_update_without_where_rejected(self, tmp_path, monkeypatch, sqlite_db):
        monkeypatch.delenv("EAIDE_APPROVAL_DRY_RUN", raising=False)
        monkeypatch.setenv("EAIDE_AUDIT_DB", str(tmp_path / "audit.sqlite"))
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        _seed_audit_approval(tmp_path, "appr_789")
        with pytest.raises(ExecuteError, match="rejected by safety"):
            asyncio.run(
                execute.run(
                    {
                        "connection": "test_sq",
                        "sql": "UPDATE users SET name = 'x'",
                        "approval_id": "appr_789",
                    }
                )
            )


class TestAuditEmitted:
    def test_audit_row_written_on_success(self, tmp_path, monkeypatch, sqlite_db):
        monkeypatch.delenv("EAIDE_APPROVAL_DRY_RUN", raising=False)
        audit_db = tmp_path / "audit.sqlite"
        monkeypatch.setenv("EAIDE_AUDIT_DB", str(audit_db))
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        _seed_audit_approval(tmp_path, "appr_audit")
        asyncio.run(
            execute.run(
                {
                    "connection": "test_sq",
                    "sql": "UPDATE users SET name='z' WHERE id = 2",
                    "approval_id": "appr_audit",
                }
            )
        )
        # audit row must exist
        conn = sqlite3.connect(audit_db)
        rows = conn.execute("SELECT action FROM audit").fetchall()
        conn.close()
        actions = {r[0] for r in rows}
        assert "db.execute.ok" in actions

    def test_audit_row_written_on_rejection(self, tmp_path, monkeypatch, sqlite_db):
        monkeypatch.delenv("EAIDE_APPROVAL_DRY_RUN", raising=False)
        audit_db = tmp_path / "audit.sqlite"
        monkeypatch.setenv("EAIDE_AUDIT_DB", str(audit_db))
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        _seed_audit_approval(tmp_path, "appr_rej")
        with pytest.raises(ExecuteError):
            asyncio.run(
                execute.run(
                    {
                        "connection": "test_sq",
                        "sql": "DROP TABLE users",
                        "approval_id": "appr_rej",
                    }
                )
            )
        conn = sqlite3.connect(audit_db)
        rows = conn.execute("SELECT action FROM audit").fetchall()
        conn.close()
        actions = {r[0] for r in rows}
        assert "db.execute.reject" in actions
