"""Tests for the schema introspection tool."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from mcp_server_database.tools import schema


@pytest.fixture
def sqlite_db(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL);
    """)
    conn.close()
    return db


class TestSqliteIntrospection:
    def test_lists_user_tables(self, sqlite_db, monkeypatch):
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        out = asyncio.run(schema.run({"connection": "test_sq"}))
        assert out["ok"] is True
        names = {t["name"] for t in out["tables"]}
        assert names == {"users", "orders"}
        # SQL internal tables excluded
        assert "sqlite_master" not in names

    def test_columns_and_types(self, sqlite_db, monkeypatch):
        monkeypatch.setenv("EAIDE_DB_DSN_TEST_SQ", f"sqlite:///{sqlite_db}")
        out = asyncio.run(schema.run({"connection": "test_sq"}))
        users = next(t for t in out["tables"] if t["name"] == "users")
        cols = {c["name"]: c for c in users["columns"]}
        assert cols["id"]["nullable"] is False
        assert cols["id"]["type"].upper() == "INTEGER"
        assert cols["name"]["nullable"] is False
        assert cols["email"]["nullable"] is True
