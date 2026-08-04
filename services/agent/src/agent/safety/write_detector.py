"""Decide whether a tool call is a read or a write — used by hitl_gate.

This is the Agent-side first filter. The MCP server (`mcp-server-database`,
`mcp-server-ssh`, etc.) re-runs its own safety pipeline (sqlglot AST,
dangerous_ops, etc.) as belt-and-braces.
"""
from __future__ import annotations

import re


_NAME_WRITE_TOKENS = (
    "write", "mutate", "update", "delete", "post", "put", "patch",
    "execute", ".exec",    # "ssh.exec" / "shell.exec" — dot-prefixed to avoid false positives
    "drop", "truncate", "alter", "create", "insert",
    "upload",               # SFTP upload
    "rename",               # file / table rename
    "merge",                # SQL MERGE / UPSERT
    "replace",              # REPLACE INTO / regex replace
    "grant", "revoke",      # DCL — privilege changes
    "call",                 # CALL stored_procedure
)


_SQL_WRITE_RE = re.compile(
    r"\b(insert|update|delete|drop|truncate|create|alter|grant|revoke"
    r"|merge|replace|call|exec|execute|rename|set\s+role)\b",
    re.IGNORECASE,
)

# Additional arg names that may carry SQL — not just "sql" and "query".
_SQL_ARG_NAMES = ("sql", "query", "statement", "command", "stmt")


def is_write_call(call: dict) -> bool:
    """Heuristic — combines name inspection + SQL keyword scan + risk level."""
    name = (call.get("name") or "").lower()
    if any(t in name for t in _NAME_WRITE_TOKENS):
        return True

    args = call.get("args") or {}
    for arg_name in _SQL_ARG_NAMES:
        sql = args.get(arg_name) or ""
        if sql and _SQL_WRITE_RE.search(sql):
            return True

    # Plan-supplied risk level is the planner's own claim — trust it.
    # "low" is excluded: it typically indicates sensitive reads (e.g. PII),
    # not writes, and treating it as a write would trigger HITL too broadly.
    risk = (call.get("risk_level") or "read").lower()
    return risk in {"medium", "high", "critical"}