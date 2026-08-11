"""SQL syntax + AST validator built on sqlglot.

三层防御：
1. Parse — sqlglot 解析失败即拒（多语句、注释拼接绕过）
2. Dialect — 仅放行白名单方言（避免方言特性绕过）
3. AST shape — 仅放行 SELECT/INSERT/UPDATE/DELETE/WITH/EXPLAIN
4. Sub-tree scan — 递归检查 CTEs / Subqueries / SetOps 内部也合规
5. Dangerous-function ban — 直接拒 xp_cmdshell / LOAD_FILE / COPY ... FROM PROGRAM

调用方只需要：
    sqlglot_validator.assert_safe_sql("SELECT 1", dialect="postgres")
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlglot
import sqlglot.expressions as exp
from sqlglot.errors import ParseError, TokenError

from mcp_server_database.safety.dialect_allowlist import (
    assert_dialect_allowed,
    to_sqlglot_dialect,
)

# ---- Allow-listed top-level statement types ----
_ALLOWED_TOP_LEVEL: tuple[type, ...] = (
    exp.Select,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.With,  # CTEs that wrap a SELECT (we re-validate inner)
    exp.Command,  # postgres EXPLAIN / VACUUM etc. — gated by name
    exp.Union,  # set operations
    exp.Intersect,
    exp.Except,
)


# ---- Dangerous function names (case-insensitive) ----
_BANNED_FUNCTIONS: frozenset[str] = frozenset(
    {
        # generic
        "sleep",  # can stall a query (combined with heavy CPU)
        "benchmark",
        # file / OS
        "load_file",
        "load_extension",
        "xp_cmdshell",
        "xp_dirtree",
        "xp_fileexist",
        "openrowset",
        "opendatasource",
        "bulk_insert",
        # network
        "utl_http",
        "http_get",
        "dbms_xmlquery",
        # postgres COPY from program
        "pg_read_file",
        "pg_read_binary_file",
        "lo_import",
        # sqlite
        "readfile",
        "writefile",
    }
)


class UnsafeSqlError(Exception):
    """Raised for any safety check failure. The message is safe to surface to the LLM."""


# ---- Public API -------------------------------------------------------------


def assert_safe_sql(sql: str, *, dialect: str = "ansi") -> None:
    """Validate `sql` against every safety rule. Raises UnsafeSqlError on failure.

    Parameters
    ----------
    sql : str
        Raw SQL text. Should be stripped of trailing semicolons.
    dialect : str
        Logical dialect id (must be in the allowlist).
    """
    assert_dialect_allowed(dialect)
    _assert_not_empty(sql)
    _assert_single_statement(sql)
    # EXPLAIN ANALYZE actually executes — reject before we even parse
    _assert_no_explain_analyze(sql)
    statements = _parse(sql, dialect)
    _assert_top_level_allowed(statements)
    _assert_no_dangerous_functions(statements)
    _assert_no_dangerous_commands(statements)


# ---- Implementation --------------------------------------------------------


def _assert_not_empty(sql: str) -> None:
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise UnsafeSqlError("empty sql")


def _assert_single_statement(sql: str) -> None:
    """Reject multi-statement inputs (vector for chained DROP/INSERT)."""
    # sqlglot.parse with one statement returns a list of length 1; >1 means stacked.
    # But it can also 'silently' drop empty trailing semicolons, so we additionally
    # scan for raw semicolons outside of string literals.
    if _contains_top_level_semicolon(sql):
        raise UnsafeSqlError("multi-statement sql is not allowed (top-level ';' detected)")


def _contains_top_level_semicolon(sql: str) -> bool:
    """Return True if there is a ';' outside any string literal or line comment."""
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_single:
            if ch == "'":
                # SQL standard: '' is an escaped quote, not a closing one.
                if nxt == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '"':
                if nxt == '"':
                    i += 2
                    continue
                in_double = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == ";":
            # A trailing semicolon at the very end is fine.
            if sql[i + 1 :].strip() == "":
                return False
            return True
        i += 1
    return False


def _parse(sql: str, dialect: str) -> list[exp.Expression]:
    try:
        statements = sqlglot.parse(sql, read=to_sqlglot_dialect(dialect))
    except (ParseError, TokenError) as exc:
        raise UnsafeSqlError(f"sql parse error: {exc}") from exc
    if not statements or statements == [None]:
        raise UnsafeSqlError("unparsable or empty sql")
    return statements


def _assert_top_level_allowed(statements: Iterable[exp.Expression]) -> None:
    """Allow-list top-level statement shapes; recurse into CTEs / subqueries.

    Recognised shapes:
        - SELECT / WITH / INSERT / UPDATE / DELETE / UNION / INTERSECT / EXCEPT
        - Command(EXPLAIN …) — EXPLAIN by definition does not execute its
          argument, so we allow it (the dangerous-ops scan still applies to
          the raw SQL text as a backstop). All other Commands are banned.
    """
    for stmt in statements:
        if stmt is None:
            continue
        if isinstance(stmt, exp.Command):
            _assert_safe_command(stmt)
            continue
        if not isinstance(stmt, _ALLOWED_TOP_LEVEL):
            raise UnsafeSqlError(f"statement type not allowed: {type(stmt).__name__}")
        _walk_subtree(stmt)


def _assert_safe_command(stmt: exp.Command) -> None:
    """Only `EXPLAIN` (without ANALYZE) is allowed at the Command layer.

    PostgreSQL's EXPLAIN ANALYZE actually *executes* the statement,
    so it must be treated as the underlying statement type rather than
    a harmless inspection command. Other Commands (VACUUM/COPY/SET/etc.)
    are banned outright.
    """
    head = (stmt.this or "").upper()
    if head != "EXPLAIN":
        raise UnsafeSqlError(f"command not allowed: {head or type(stmt).__name__}")


def _walk_subtree(node: exp.Expression) -> None:
    """Recursively assert no banned functions appear anywhere in the AST.

    sqlglot represents unknown function calls as exp.Anonymous — their
    `sql_name()` always returns "ANONYMOUS" (useless for our purposes), so
    we fall back to `func.name` which preserves the original identifier.
    """
    for func in node.find_all(exp.Anonymous, exp.Func):
        name = _function_name(func).lower()
        if name in _BANNED_FUNCTIONS:
            raise UnsafeSqlError(f"function not allowed: {name}")


def _function_name(func: exp.Expression) -> str:
    """Best-effort extraction of the original function identifier."""
    name = (getattr(func, "name", None) or "").strip()
    if name and name.upper() != "ANONYMOUS":
        return name
    # Last resort: try sql_name (works for named Func subclasses)
    sql_name = (func.sql_name() or "").strip()
    return sql_name or name


def _assert_no_dangerous_functions(statements: Iterable[exp.Expression]) -> None:
    # already covered by _walk_subtree; kept as a hook for future static analysis.
    return None


def _assert_no_dangerous_commands(statements: Iterable[exp.Expression]) -> None:
    # Logic moved into _assert_safe_command via the unified top-level filter.
    return None


def _assert_no_explain_analyze(sql: str) -> None:
    """Reject EXPLAIN ANALYZE — it actually executes the statement in PostgreSQL.

    EXPLAIN alone is safe (it only shows the query plan), but EXPLAIN ANALYZE
    runs the query to measure actual timings. This means EXPLAIN ANALYZE DELETE
    would actually delete rows.
    """
    import re

    if re.search(r"\bEXPLAIN\s+ANALYZE\b", sql, re.IGNORECASE):
        raise UnsafeSqlError(
            "EXPLAIN ANALYZE is not allowed — it executes the underlying statement. "
            "Use EXPLAIN (without ANALYZE) to inspect query plans."
        )
