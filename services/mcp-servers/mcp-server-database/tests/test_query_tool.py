"""Tests for the query tool — exercises SQLite path end-to-end (no external deps)."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from mcp_server_database.tools import query
from mcp_server_database.tools.query import QueryError


@pytest.fixture
def sqlite_db(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE users (
            id    INTEGER PRIMARY KEY,
            name  TEXT NOT NULL,
            email TEXT,
            bio   TEXT
        );
        INSERT INTO users VALUES (1, 'alice', 'a@x.com', 'short bio');
        INSERT INTO users VALUES (2, 'bob',   'b@x.com', NULL);
    """)
    big_bio = "x" * 5000
    conn.execute("INSERT INTO users VALUES (3, 'carol', NULL, ?)", (big_bio,))
    conn.commit()
    conn.close()
    return db


class TestSqliteHappyPath:
    def test_select_returns_rows(self, sqlite_db, monkeypatch):
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        out = asyncio.run(
            query.run(
                {
                    "connection": "test_sq",
                    "sql": "SELECT id, name FROM users ORDER BY id",
                }
            )
        )
        assert out["ok"] is True
        assert out["columns"] == ["id", "name"]
        assert [r[0] for r in out["rows"]] == [1, 2, 3]
        assert out["rows_returned"] == 3

    def test_null_normalised(self, sqlite_db, monkeypatch):
        # Row 2 in fixture has email='b@x.com' (not NULL); row 1 has it set;
        # verify NULL serialisation on a column that IS NULL — `bio`.
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        out = asyncio.run(
            query.run(
                {
                    "connection": "test_sq",
                    "sql": "SELECT id, bio FROM users WHERE id = 2",
                }
            )
        )
        assert out["rows"][0] == [2, None]


class TestSqliteTruncation:
    def test_per_cell_truncates_large_text(self, sqlite_db, monkeypatch):
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        out = asyncio.run(
            query.run(
                {
                    "connection": "test_sq",
                    "sql": "SELECT id, bio FROM users WHERE id = 3",
                    "_per_cell_bytes": 64,
                }
            )
        )
        assert out["truncated"] is True
        bio = out["rows"][0][1]
        assert isinstance(bio, str)
        assert bio.endswith("…[truncated]")

    def test_row_limit_applied(self, sqlite_db, monkeypatch):
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        out = asyncio.run(
            query.run(
                {
                    "connection": "test_sq",
                    "sql": "SELECT id FROM users ORDER BY id",
                    "_row_limit": 2,
                }
            )
        )
        assert out["rows_returned"] == 2
        assert out["truncated"] is True


class TestSqliteUnsafeRejected:
    def test_drop_rejected(self, sqlite_db, monkeypatch):
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        with pytest.raises(QueryError, match="unsafe sql"):
            asyncio.run(
                query.run(
                    {
                        "connection": "test_sq",
                        "sql": "DROP TABLE users",
                    }
                )
            )

    def test_update_without_where_rejected(self, sqlite_db, monkeypatch):
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        # db.query is strictly read-only; write SQL is rejected with a clearer
        # message than the generic "unsafe sql".
        with pytest.raises(QueryError, match="read-only"):
            asyncio.run(
                query.run(
                    {
                        "connection": "test_sq",
                        "sql": "UPDATE users SET name = 'x'",
                    }
                )
            )

    def test_multi_statement_rejected(self, sqlite_db, monkeypatch):
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        with pytest.raises(QueryError, match="unsafe sql"):
            asyncio.run(
                query.run(
                    {
                        "connection": "test_sq",
                        "sql": "SELECT 1; DELETE FROM users",
                    }
                )
            )


class TestSqliteConnectionError:
    def test_missing_connection(self, monkeypatch):
        with pytest.raises(KeyError):
            asyncio.run(
                query.run(
                    {
                        "connection": "nope",
                        "sql": "SELECT 1",
                    }
                )
            )
