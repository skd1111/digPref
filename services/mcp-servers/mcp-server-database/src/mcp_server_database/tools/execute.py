"""db.execute — write statement execution.

Hard contract:
    - The upstream LangGraph hitl_gate MUST have approved this call.
    - The caller MUST include an `approval_id` argument; we verify it against
      the shared audit log / Redis before dispatching.
    - Every execution is wrapped in a transaction with automatic rollback
      on any error.
    - sqlglot validator + dangerous_ops still run (belt-and-braces).
    - The statement is dispatched via *prepared* parameters only —
      no string concatenation.
    - Every successful and failed execution is appended to the audit log.

Returns a dict shaped like:
    {
      "ok": True,
      "approval_id": "...",
      "rows_affected": 12,
      "duration_ms": 87,
    }
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from mcp_server_database.audit.emitter import audit
from mcp_server_database.config import Settings
from mcp_server_database.connections import Connections
from mcp_server_database.safety.dangerous_ops import (
    DestructiveOpError,
    assert_no_destructive,
)
from mcp_server_database.safety.dialect_allowlist import dialect_from_connection
from mcp_server_database.safety.sqlglot_validator import (
    UnsafeSqlError,
    assert_safe_sql,
)


class ExecuteError(Exception):
    """Surface to LLM as 'rejected by safety policy'."""


class ApprovalMissingError(ExecuteError):
    """Raised when approval_id is missing or invalid."""


# ---- Public entry point ----------------------------------------------------

async def run(args: dict) -> dict:
    started = time.monotonic()
    approval_id = args.get("approval_id")
    if not approval_id:
        raise ApprovalMissingError(
            "approval_id is required for db.execute (HITL gate)"
        )

    # Verify the approval is real. Implementation: check that an
    # `approval.decision=approve` row exists for this id in the shared audit log.
    # For now: simple env-based gating (dev mode).
    if not _verify_approval(approval_id, args):
        raise ApprovalMissingError(
            f"approval_id {approval_id!r} not found or not approved"
        )

    # Resolve connection
    connection = args["connection"]
    conns = Connections(Settings())
    dsn = conns.dsn(connection)
    dialect = dialect_from_connection(connection)

    sql = args["sql"]
    params = args.get("params") or []

    # ---- safety pipeline ----
    try:
        assert_safe_sql(sql, dialect=dialect)
        assert_no_destructive(sql, dialect=dialect)
    except (UnsafeSqlError, DestructiveOpError) as exc:
        audit("db.execute.reject", {
            "approval_id": approval_id,
            "connection": connection,
            "reason": str(exc),
            "sql_preview": sql[:200],
        })
        raise ExecuteError(f"rejected by safety policy: {exc}") from exc

    # ---- execute with hard timeout ----
    timeout_sec = Settings().tool_timeout_sec
    try:
        affected = await asyncio.wait_for(
            _execute(dsn, dialect, sql, params),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError as exc:
        audit("db.execute.timeout", {
            "approval_id": approval_id,
            "connection": connection,
            "sql_preview": sql[:200],
            "timeout_sec": timeout_sec,
        })
        raise ExecuteError(f"execute exceeded {timeout_sec}s timeout") from exc
    except Exception as exc:  # noqa: BLE001
        audit("db.execute.error", {
            "approval_id": approval_id,
            "connection": connection,
            "sql_preview": sql[:200],
            "error": str(exc),
        })
        raise ExecuteError(f"execute failed: {exc}") from exc

    # ---- success audit ----
    audit("db.execute.ok", {
        "approval_id": approval_id,
        "connection": connection,
        "rows_affected": affected,
        "sql_preview": sql[:200],
        "duration_ms": int((time.monotonic() - started) * 1000),
    })

    return {
        "ok": True,
        "approval_id": approval_id,
        "rows_affected": affected,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "dialect": dialect,
        "connection": connection,
    }


# ---- Implementation --------------------------------------------------------

def _verify_approval(approval_id: str, args: dict) -> bool:
    """Verify that `approval_id` exists, was approved, and matches the SQL.

    Three strategies:
        - Production: query the shared audit SQLite (read-only) and look for
          an `approval.decision` row with `decision='approve'` matching this id.
          Additionally verify the SQL hash matches to prevent replay attacks.
        - Dev / test: `EAIDE_APPROVAL_DRY_RUN=1` bypasses verification
          (this env var MUST never be set in production — it is logged as a
          warning on every bypass).
    """
    if os.environ.get("EAIDE_APPROVAL_DRY_RUN") == "1":
        import logging
        logging.getLogger("mcp_server_database").warning(
            "EAIDE_APPROVAL_DRY_RUN=1 — ALL approval checks bypassed. "
            "This MUST never be set in production."
        )
        return True
    return _approval_audit_lookup(approval_id, args.get("sql", ""))


def _approval_audit_lookup(approval_id: str, sql: str) -> bool:
    """Read the shared audit.sqlite (read-only) and look up the approval.

    Verifies:
      1. The approval_id exists and was approved.
      2. The approved SQL statement matches (by hash) to prevent replay attacks
         where an attacker reuses an approval for a different statement.
      3. The approval is not expired (> 5 minutes old).

    Scans all recent audit entries (no artificial LIMIT that could expire
    valid approvals under high concurrency).
    """
    import hashlib
    import json
    import sqlite3
    import time
    from pathlib import Path

    db_path = Path(os.environ.get("EAIDE_AUDIT_DB", "audit.sqlite"))
    if not db_path.exists():
        return False

    sql_hash = hashlib.sha256(sql.encode()).hexdigest()[:16]
    max_age_sec = 300  # 5 minutes — approvals expire after this

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.execute(
            "SELECT payload, created_at FROM audit "
            "WHERE action IN ('approval.decision','approval.approve') "
            "ORDER BY id DESC",
        )
        for (payload, created_at) in cur.fetchall():
            try:
                d = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                continue
            if d.get("approval_id") != approval_id:
                continue
            if d.get("decision") != "approve":
                continue
            # Check expiry
            if created_at:
                try:
                    # created_at is ISO-8601 string
                    from datetime import datetime, timezone
                    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - created).total_seconds()
                    if age > max_age_sec:
                        return False  # expired
                except (ValueError, TypeError):
                    pass  # can't parse timestamp — accept
            # Verify SQL binding (if the approval recorded the SQL hash)
            approved_hash = d.get("sql_hash") or d.get("statement_hash") or ""
            if approved_hash and approved_hash != sql_hash:
                return False  # SQL mismatch — replay attack
            return True
    except sqlite3.Error:
        return False
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return False


# ---- Driver dispatch -------------------------------------------------------

async def _execute(dsn: str, dialect: str, sql: str, params: list) -> int:
    """Dispatch to the right driver. Returns rows affected."""
    if dialect in {"postgres", "redshift"}:
        return await _pg(dsn, sql, params)
    if dialect == "mysql":
        return await _my(dsn, sql, params)
    if dialect == "sqlite":
        return await _sq(dsn, sql, params)
    raise ExecuteError(f"unsupported dialect for execute: {dialect}")


async def _pg(dsn: str, sql: str, params: list) -> int:
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            status = await conn.execute(sql, *params)
        # status is e.g. 'UPDATE 5'
        return _parse_rows_affected(status)
    finally:
        await conn.close()


async def _my(dsn: str, sql: str, params: list) -> int:
    import aiomysql
    from urllib.parse import urlparse
    u = urlparse(dsn if "://" in dsn else f"mysql://{dsn}")
    conn = await aiomysql.connect(
        host=u.hostname or "127.0.0.1", port=u.port or 3306,
        user=u.username or "", password=u.password or "",
        db=(u.path or "/").lstrip("/"), autocommit=False,
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute("START TRANSACTION")
            try:
                affected = await cur.execute(sql, params or ())
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return int(affected)
    finally:
        await conn.close()


async def _sq(dsn: str, sql: str, params: list) -> int:
    import aiosqlite
    # 支持多种 SQLite DSN 格式（与 query.py 保持一致）
    raw = dsn
    if ":///" in raw:
        raw = raw.split(":///", 1)[-1]
    elif raw.startswith("file:"):
        raw = raw[len("file:"):]
        if raw.startswith("///"):
            raw = raw[3:]
        elif raw.startswith("//"):
            raw = raw[2:]
    path = raw.split("?", 1)[0]
    db = await aiosqlite.connect(path)
    try:
        await db.execute("BEGIN")
        try:
            cur = await db.execute(sql, params or ())
            await db.commit()
            return cur.rowcount or 0
        except Exception:
            await db.rollback()
            raise
    finally:
        await db.close()


def _parse_rows_affected(status: str) -> int:
    """asyncpg returns a status string like 'UPDATE 5'."""
    parts = (status or "").split()
    if len(parts) >= 2 and parts[-1].isdigit():
        return int(parts[-1])
    return 0