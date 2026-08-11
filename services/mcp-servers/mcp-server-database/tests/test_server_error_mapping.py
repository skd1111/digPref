"""End-to-end smoke test of the server dispatcher."""

from __future__ import annotations

import pytest
from mcp_server_database.safety.dangerous_ops import DestructiveOpError
from mcp_server_database.safety.sqlglot_validator import UnsafeSqlError
from mcp_server_database.server import (
    _attach_meta,
    _error_response,
    _map_exception,
    _prevalidate_sql,
)


class TestPrevalidateSql:
    def test_empty_sql(self):
        with pytest.raises(UnsafeSqlError, match="empty"):
            _prevalidate_sql({"sql": "  ", "connection": "x_pg"})

    def test_valid_select(self):
        _prevalidate_sql({"sql": "SELECT 1", "connection": "x_pg"})

    def test_drop_rejected(self):
        with pytest.raises(UnsafeSqlError):
            _prevalidate_sql({"sql": "DROP TABLE users", "connection": "x_pg"})

    def test_execute_additional_destructive_check(self):
        # SELECT 1 passes the sqlglot validator; dangerous_ops only triggers on
        # the write path. Use an UPDATE without WHERE — caught by both checks.
        with pytest.raises(DestructiveOpError, match="UPDATE without WHERE"):
            _prevalidate_sql(
                {
                    "sql": "UPDATE users SET name = 'x'",
                    "connection": "x_pg",
                    "approval_id": "abc",
                }
            )


class TestErrorMapping:
    def test_unsafe_maps_to_unsafe_code(self):
        exc = _map_exception(UnsafeSqlError("bad"))
        assert exc.code == "UNSAFE_SQL"

    def test_destructive_maps(self):
        exc = _map_exception(DestructiveOpError("bad"))
        assert exc.code == "DESTRUCTIVE_OP"

    def test_unknown_maps_to_generic(self):
        exc = _map_exception(RuntimeError("oops"))
        assert exc.code == "ERROR"


class TestResponseShape:
    def test_error_response_shape(self):
        r = _error_response("X", "msg", 0.0, "db.query")
        assert r["ok"] is False
        assert r["code"] == "X"
        assert r["message"] == "msg"
        assert r["tool"] == "db.query"
        assert "duration_ms" in r

    def test_attach_meta_fills_defaults(self):
        r = _attach_meta({"ok": True, "columns": [], "rows": []}, 0.0, "db.query")
        assert r["tool"] == "db.query"
        assert "duration_ms" in r
