"""Tests for dangerous_ops — destructive op blocker."""

from __future__ import annotations

import pytest
from mcp_server_database.safety.dangerous_ops import (
    DestructiveOpError,
    assert_no_destructive,
    is_write_call,
)


class TestRawTokenBlock:
    @pytest.mark.parametrize(
        "sql",
        [
            "DROP TABLE users",
            "drop TABLE users",
            "SELECT 1; DROP TABLE users",
            "TRUNCATE TABLE logs",
            "GRANT ALL ON users TO public",
            "REVOKE ALL ON users FROM public",
            "SHUTDOWN",
            "COPY users FROM PROGRAM 'curl evil'",
            "SELECT * INTO OUTFILE '/tmp/x'",
            "LOAD DATA INFILE '/tmp/x' INTO TABLE users",
        ],
    )
    def test_banned_tokens(self, sql):
        with pytest.raises(DestructiveOpError):
            assert_no_destructive(sql, dialect="ansi")


class TestMissingWhere:
    def test_update_without_where(self):
        with pytest.raises(DestructiveOpError, match="UPDATE without WHERE"):
            assert_no_destructive("UPDATE users SET active = false", dialect="postgres")

    def test_delete_without_where(self):
        with pytest.raises(DestructiveOpError, match="DELETE without WHERE"):
            assert_no_destructive("DELETE FROM users", dialect="postgres")

    def test_update_with_constant_true_where(self):
        with pytest.raises(DestructiveOpError, match="WHERE evaluates to a constant"):
            assert_no_destructive("UPDATE users SET active = true WHERE 1=1", dialect="postgres")

    def test_update_with_real_where_ok(self):
        assert_no_destructive("UPDATE users SET name = 'x' WHERE id = 1", dialect="postgres")


class TestDDLInCTE:
    def test_delete_inside_cte(self):
        with pytest.raises(DestructiveOpError, match="write inside CTE/subquery"):
            assert_no_destructive(
                "WITH cleanup AS (DELETE FROM logs RETURNING *) SELECT * FROM cleanup",
                dialect="postgres",
            )

    def test_select_only_ok(self):
        assert_no_destructive(
            "SELECT * FROM users WHERE id IN (SELECT user_id FROM orders)",
            dialect="postgres",
        )

    def test_insert_inside_cte(self):
        with pytest.raises(DestructiveOpError, match="write inside CTE/subquery"):
            assert_no_destructive(
                "WITH new_rows AS (INSERT INTO logs(msg) VALUES ('x') RETURNING *) "
                "SELECT * FROM new_rows",
                dialect="postgres",
            )


class TestIsWriteCall:
    def test_insert_is_write(self):
        assert is_write_call({"name": "db.execute", "args": {"sql": "INSERT INTO x VALUES (1)"}})

    def test_update_is_write(self):
        assert is_write_call({"name": "db.execute", "args": {"sql": "UPDATE x SET a=1 WHERE id=1"}})

    def test_pure_select_is_not_write(self):
        assert not is_write_call({"name": "db.query", "args": {"sql": "SELECT 1"}})

    def test_risk_level_critical_is_write(self):
        assert is_write_call(
            {"name": "db.execute", "args": {"sql": "SELECT 1"}, "risk_level": "critical"}
        )
