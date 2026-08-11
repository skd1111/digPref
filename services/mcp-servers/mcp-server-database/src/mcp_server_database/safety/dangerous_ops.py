"""High-risk operation blocker — even when HITL approval was granted upstream.

Hard-bans:
    DROP / TRUNCATE / GRANT / REVOKE / SHUTDOWN
    UPDATE/DELETE without WHERE clause
    CTEs that contain write operations
    Subqueries that contain write operations
    COPY ... FROM PROGRAM
    VACUUM / REINDEX / CLUSTER
    ALTER on system schemas
    Set operations that mix reads and writes

Layered defence: this runs *after* sqlglot_validator.assert_safe_sql, so the
input is already known to be syntactically valid and well-formed. We add
semantic-level guarantees here.
"""

from __future__ import annotations

import re

import sqlglot
import sqlglot.expressions as exp

from mcp_server_database.safety.dialect_allowlist import to_sqlglot_dialect

# ---- Banned tokens (raw-text belt-and-braces) ------------------------------
_BANNED_TOKENS: tuple[str, ...] = (
    r"\bdrop\b",
    r"\btruncate\b",
    r"\bgrant\b",
    r"\brevoke\b",
    r"\bshutdown\b",
    r"\bcheckpoint\b",
    r"\breindex\b",
    r"\bcluster\b",
    r"\bvacuum\b",
    r"\bcopy\b\s+\w+\s+from\s+program",
    r"\binto\s+outfile\b",
    r"\bload\s+data\b",
)

_BANNED_RE = re.compile("|".join(_BANNED_TOKENS), re.IGNORECASE)


class DestructiveOpError(Exception):
    """Raised when a call tries to perform a destructive / privileged operation."""


# ---- Public API -------------------------------------------------------------


def assert_no_destructive(sql: str, *, dialect: str = "ansi") -> None:
    """Run all hard-ban checks. Raises DestructiveOpError on failure."""
    _assert_no_banned_tokens(sql)
    _assert_no_ddl_in_subtree(sql, dialect)


def is_write_call(call: dict) -> bool:
    """Lightweight check used by the Agent to decide whether to trigger HITL."""
    name = (call.get("name") or "").lower()
    if any(t in name for t in ("write", "mutate", "update", "delete", "post", "put", "execute")):
        return True
    sql = (call.get("args") or {}).get("sql") or ""
    if sql:
        try:
            for stmt in sqlglot.parse(sql):
                if isinstance(stmt, (exp.Insert, exp.Update, exp.Delete)):
                    return True
        except Exception:
            return False
    # "low" 是敏感读取（如 PII）而非写入，不应触发 HITL
    return call.get("risk_level") in {"medium", "high", "critical"}


# ---- Implementation --------------------------------------------------------


def _assert_no_banned_tokens(sql: str) -> None:
    m = _BANNED_RE.search(sql)
    if m:
        raise DestructiveOpError(
            f"destructive operation not allowed via MCP: matched {m.group(0)!r}"
        )


def _assert_no_ddl_in_subtree(sql: str, dialect: str) -> None:
    """Walk the entire AST; any DDL/DCL OR write inside a CTE/subquery is rejected."""
    statements = sqlglot.parse(sql, read=to_sqlglot_dialect(dialect))
    for stmt in statements:
        if stmt is None:
            continue
        for node in stmt.walk():
            if isinstance(
                node, (exp.Create, exp.Drop, exp.TruncateTable, exp.Grant, exp.Revoke, exp.Alter)
            ):
                raise DestructiveOpError(f"DDL/DCL inside query not allowed: {type(node).__name__}")
            # Writes nested inside a CTE / subquery are also rejected — the
            # outer SELECT would otherwise smuggle them past the validator.
            if isinstance(node, (exp.Insert, exp.Update, exp.Delete)) and node is not stmt:
                raise DestructiveOpError(
                    f"write inside CTE/subquery not allowed: {type(node).__name__}"
                )
        _assert_write_always_has_where(stmt)


def _assert_write_always_has_where(stmt: exp.Expression) -> None:
    """UPDATE / DELETE must always carry a WHERE — no exceptions, no implicit
    'WHERE TRUE' shortcuts like `WHERE 1=1` or `WHERE TRUE`."""
    if not isinstance(stmt, (exp.Update, exp.Delete)):
        return
    kind = type(stmt).__name__.upper()
    where = stmt.args.get("where")
    if where is None:
        raise DestructiveOpError(
            f"{kind} without WHERE is forbidden — add an explicit WHERE clause"
        )
    # Also forbid constant-true WHEREs
    cmp = where.this if isinstance(where, exp.Where) else where
    if _is_purely_literal(cmp):
        raise DestructiveOpError(f"{kind} WHERE evaluates to a constant — forbidden")


def _is_purely_literal(node: exp.Expression | None) -> bool:
    """True if the entire expression is built from literal values (no columns)."""
    if node is None:
        return True
    if isinstance(node, (exp.Literal, exp.Boolean)):
        return True
    # Recurse into compound expressions; return True only if every leaf is literal.
    if isinstance(
        node,
        (
            exp.And,
            exp.Or,
            exp.EQ,
            exp.NEQ,
            exp.GT,
            exp.GTE,
            exp.LT,
            exp.LTE,
            exp.Not,
            exp.Paren,
            exp.Neg,
        ),
    ):
        children = [c for c in node.iter_expressions() if c is not None]
        return all(_is_purely_literal(c) for c in children) if children else True
    # Anything that mentions a Column is NOT literal
    if node.find(exp.Column):
        return False
    return True
