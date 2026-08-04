"""Tests for sqlglot_validator — every common SQL-injection trick."""
from __future__ import annotations

import pytest

from mcp_server_database.safety.sqlglot_validator import (
    UnsafeSqlError,
    assert_safe_sql,
)


class TestHappyPath:
    def test_simple_select(self):
        assert_safe_sql("SELECT 1", dialect="ansi")

    def test_select_with_where(self):
        assert_safe_sql(
            "SELECT id, name FROM users WHERE id = $1 AND active = true",
            dialect="postgres",
        )

    def test_select_with_cte(self):
        assert_safe_sql(
            "WITH active AS (SELECT * FROM users WHERE active) "
            "SELECT count(*) FROM active",
            dialect="postgres",
        )

    def test_insert(self):
        assert_safe_sql(
            "INSERT INTO logs(message, ts) VALUES ($1, NOW())",
            dialect="postgres",
        )

    def test_update_with_where(self):
        assert_safe_sql(
            "UPDATE users SET name = $1 WHERE id = $2",
            dialect="postgres",
        )

    def test_delete_with_where(self):
        assert_safe_sql(
            "DELETE FROM sessions WHERE created_at < $1",
            dialect="postgres",
        )

    def test_union_all(self):
        assert_safe_sql(
            "(SELECT id FROM a) UNION ALL (SELECT id FROM b)",
            dialect="postgres",
        )

    def test_explain_select(self):
        assert_safe_sql("EXPLAIN SELECT * FROM users", dialect="postgres")


class TestMultiStatement:
    def test_chained_drop_after_select(self):
        with pytest.raises(UnsafeSqlError, match="multi-statement"):
            assert_safe_sql("SELECT 1; DROP TABLE users", dialect="postgres")

    def test_chained_delete_after_select(self):
        with pytest.raises(UnsafeSqlError, match="multi-statement"):
            assert_safe_sql(
                "SELECT * FROM logs; DELETE FROM logs WHERE id < 100",
                dialect="postgres",
            )

    def test_chained_with_line_comment(self):
        # Comment after first statement does NOT hide the second semicolon.
        with pytest.raises(UnsafeSqlError, match="multi-statement"):
            assert_safe_sql(
                "SELECT 1 -- comment\n; DROP TABLE users",
                dialect="postgres",
            )


class TestDDLBlocked:
    def test_drop_table(self):
        with pytest.raises(UnsafeSqlError, match="statement type not allowed"):
            assert_safe_sql("DROP TABLE users", dialect="postgres")

    def test_truncate(self):
        with pytest.raises(UnsafeSqlError):
            assert_safe_sql("TRUNCATE TABLE logs", dialect="postgres")

    def test_create_index(self):
        with pytest.raises(UnsafeSqlError):
            assert_safe_sql("CREATE INDEX idx ON users(name)", dialect="postgres")

    def test_alter_table(self):
        with pytest.raises(UnsafeSqlError):
            assert_safe_sql("ALTER TABLE users ADD COLUMN x INT", dialect="postgres")


class TestDangerousFunctions:
    def test_xp_cmdshell(self):
        with pytest.raises(UnsafeSqlError, match="function not allowed"):
            assert_safe_sql(
                "SELECT * FROM users WHERE name = xp_cmdshell('whoami')",
                dialect="tsql",
            )

    def test_load_file(self):
        with pytest.raises(UnsafeSqlError, match="function not allowed"):
            assert_safe_sql(
                "SELECT LOAD_FILE('/etc/passwd')",
                dialect="mysql",
            )

    def test_pg_read_file(self):
        with pytest.raises(UnsafeSqlError, match="function not allowed"):
            assert_safe_sql(
                "SELECT pg_read_file('/etc/passwd')",
                dialect="postgres",
            )


class TestDangerousCommands:
    def test_vacuum(self):
        # VACUUM parses as Command(this="VACUUM") → rejected by _assert_safe_command
        with pytest.raises(UnsafeSqlError, match="command not allowed"):
            assert_safe_sql("VACUUM FULL users", dialect="postgres")

    def test_copy(self):
        # COPY parses as exp.Copy (its own AST node) → rejected by top-level filter
        with pytest.raises(UnsafeSqlError, match="statement type not allowed"):
            assert_safe_sql("COPY users FROM '/tmp/x.csv'", dialect="postgres")

    def test_set(self):
        # SET parses as exp.Set → rejected by top-level filter
        with pytest.raises(UnsafeSqlError, match="statement type not allowed"):
            assert_safe_sql("SET search_path = public", dialect="postgres")

    def test_explain_on_update(self):
        # EXPLAIN any-statement is allowed at the parser layer (EXPLAIN never
        # executes its argument). The dangerous-ops scan still rejects raw
        # UPDATE/DELETE without WHERE inside the EXPLAIN body via the raw SQL
        # belt-and-braces check.
        # Here the inner has WHERE, so nothing trips.
        assert_safe_sql(
            "EXPLAIN UPDATE users SET name = 'x' WHERE id = 1", dialect="postgres"
        )

    def test_explain_on_select_ok(self):
        # EXPLAIN SELECT remains a Command but inner is a Select → allowed
        assert_safe_sql("EXPLAIN SELECT * FROM users", dialect="postgres")


class TestDialect:
    def test_unknown_dialect_rejected(self):
        from mcp_server_database.safety.dialect_allowlist import (
            UnsupportedDialectError,
            assert_dialect_allowed,
        )
        with pytest.raises(UnsupportedDialectError):
            assert_dialect_allowed("oracle")

    def test_dialect_inference(self):
        from mcp_server_database.safety.dialect_allowlist import (
            dialect_from_connection,
        )
        assert dialect_from_connection("orders_pg") == "postgres"
        assert dialect_from_connection("billing_my") == "mysql"
        assert dialect_from_connection("local_sq") == "sqlite"
        assert dialect_from_connection("unknown") == "ansi"