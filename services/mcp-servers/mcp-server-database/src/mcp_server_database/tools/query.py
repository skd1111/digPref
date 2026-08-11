"""db.query — read-only SQL execution.

Responsibilities:
    1. Look up the connection DSN (resolved by the upstream Rust credential vault
       and injected into this process's env as EAIDE_DB_DSN_<NAME>).
    2. Infer the SQL dialect from the connection name suffix.
    3. Run sqlglot_validator.assert_safe_sql on the user-supplied SQL.
    4. Apply read-only enforcement at the session level.
    5. Open a driver-specific connection, execute the query with a hard timeout,
       and normalise rows.
    6. Apply row + byte-size truncation, return a structured response.

Returns a dict shaped like:
    {
      "ok": True,
      "columns": ["id", "name", ...],
      "rows":    [[1, "alice"], ...],
      "truncated": False,
      "rows_returned": 12,
      "rows_dropped_by_byte_cap": 0,
      "duration_ms": 87,
    }
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import time
from typing import Any

from mcp_server_database.config import Settings
from mcp_server_database.connections import Connections
from mcp_server_database.limit.byte_size import from_args as truncate_from_args
from mcp_server_database.limit.byte_size import truncate_rows
from mcp_server_database.limit.row_limit import from_args as row_limit_from_args
from mcp_server_database.safety.dangerous_ops import (
    DestructiveOpError,
    assert_no_destructive,
)
from mcp_server_database.safety.dangerous_ops import (
    is_write_call as is_write_sql,
)
from mcp_server_database.safety.dialect_allowlist import dialect_from_connection
from mcp_server_database.safety.readonly_enforce import (
    ReadOnlyViolationError,
    force_readonly_dsn,
    wrap_readonly,
)
from mcp_server_database.safety.sqlglot_validator import (
    UnsafeSqlError,
    assert_safe_sql,
)


class QueryError(Exception):
    """Wraps any failure that should surface to the LLM as a 'try again' hint."""


# ---- Public entry point ----------------------------------------------------


async def run(args: dict) -> dict:
    started = time.monotonic()
    try:
        return await _run(args, started)
    except _Retryable as exc:
        # Errors that the LLM should see and (potentially) auto-repair.
        raise QueryError(str(exc)) from exc


# ---- Implementation --------------------------------------------------------


async def _run(args: dict, started: float) -> dict:
    # ---- 1. resolve connection + dialect ----
    connection = args["connection"]
    settings = Settings()
    conns = Connections(settings)
    raw_dsn = conns.dsn(connection)
    dialect = dialect_from_connection(connection)
    dsn = force_readonly_dsn(raw_dsn, dialect)

    # ---- 2. SQL safety ----
    sql = args["sql"]
    params = args.get("params") or []
    try:
        assert_safe_sql(sql, dialect=dialect)
        # db.query is strictly read-only — reject any write SQL upfront,
        # regardless of whether the connection would allow it.
        if is_write_sql({"name": "db.query", "args": {"sql": sql}}):
            raise _Retryable(
                "db.query is read-only; use db.execute for writes "
                "(which requires an HITL approval_id)"
            )
        assert_no_destructive(sql, dialect=dialect)
    except UnsafeSqlError as exc:
        raise _Retryable(f"unsafe sql rejected: {exc}")
    except DestructiveOpError as exc:
        raise _Retryable(f"unsafe sql rejected: {exc}")

    # ---- 3. read-only session wrap ----
    sql = wrap_readonly(dsn, sql, dialect)

    # ---- 4. execute with timeout ----
    timeout_sec = settings.tool_timeout_sec
    row_limit = row_limit_from_args(args)
    trunc_cfg = truncate_from_args(args)

    try:
        columns, rows = await asyncio.wait_for(
            _execute(dsn, dialect, sql, params, row_limit + 1),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError as exc:
        raise _Retryable(f"query exceeded {timeout_sec}s timeout") from exc
    except ReadOnlyViolationError as exc:
        raise _Retryable(f"readonly violation: {exc}") from exc

    # ---- 5. normalise rows ----
    rows = [_normalise_row(r) for r in rows]

    # ---- 6. truncation (row + byte) ----
    truncated_rows, any_truncated, dropped = truncate_rows(rows, trunc_cfg)
    truncated = any_truncated or len(truncated_rows) > row_limit
    truncated_rows = truncated_rows[:row_limit]

    return {
        "ok": True,
        "columns": columns,
        "rows": truncated_rows,
        "truncated": truncated,
        "rows_returned": len(truncated_rows),
        "rows_dropped_by_row_cap": max(0, len(rows) - len(truncated_rows)),
        "rows_dropped_by_byte_cap": dropped,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "dialect": dialect,
        "connection": connection,
    }


# ---- Driver dispatch -------------------------------------------------------


async def _execute(
    dsn: str, dialect: str, sql: str, params: list, row_cap: int
) -> tuple[list[str], list[list[Any]]]:
    if dialect in {"postgres", "redshift"}:
        return await _pg(dsn, sql, params, row_cap)
    if dialect == "mysql":
        return await _my(dsn, sql, params, row_cap)
    if dialect == "sqlite":
        return await _sq(dsn, sql, params, row_cap)
    raise _Retryable(f"unsupported dialect for query: {dialect}")


async def _pg(dsn: str, sql: str, params: list, row_cap: int):
    import asyncpg

    try:
        conn = await asyncpg.connect(dsn)
    except Exception as exc:
        raise _Retryable(f"postgres connect failed: {exc}") from exc
    try:
        columns: list[str] = []
        rows: list[list] = []
        async with conn.transaction():
            async for record in conn.cursor(sql, *params):
                if not columns:
                    columns = list(record.keys())
                if len(rows) < row_cap:
                    rows.append(list(record))
                else:
                    break
    except asyncpg.PostgresError as exc:
        raise _Retryable(f"postgres error: {exc}") from exc
    finally:
        await conn.close()
    return columns, rows


async def _my(dsn: str, sql: str, params: list, row_cap: int):
    import aiomysql

    parsed = _parse_mysql_dsn(dsn)
    try:
        conn = await aiomysql.connect(
            host=parsed["host"],
            port=parsed.get("port", 3306),
            user=parsed["user"],
            password=parsed["password"],
            db=parsed["database"],
            autocommit=True,
            connect_timeout=max(1, _timeout_left()),
        )
    except Exception as exc:
        raise _Retryable(f"mysql connect failed: {exc}") from exc
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql, params or ())
            rows = await cur.fetchmany(row_cap)
            cols = [d[0] for d in cur.description] if cur.description else []
    except Exception as exc:
        raise _Retryable(f"mysql error: {exc}") from exc
    finally:
        await conn.close()
    return cols, [list(r) for r in rows]


async def _sq(dsn: str, sql: str, params: list, row_cap: int):
    import aiosqlite

    # 支持多种 SQLite DSN 格式：
    #   sqlite:///path/to/db      → path/to/db
    #   file:path/to/db           → path/to/db
    #   file:///path/to/db        → path/to/db (URI 编码)
    #   /absolute/path            → /absolute/path
    raw = dsn
    if ":///" in raw:
        raw = raw.split(":///", 1)[-1]
    elif raw.startswith("file:"):
        raw = raw[len("file:") :]
        if raw.startswith("///"):
            raw = raw[3:]
        elif raw.startswith("//"):
            raw = raw[2:]
    path = raw.split("?", 1)[0]
    try:
        db = await aiosqlite.connect(path)
    except Exception as exc:
        raise _Retryable(f"sqlite connect failed: {exc}") from exc
    try:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params or ()) as cur:
            rows = await cur.fetchmany(row_cap)
            cols = [d[0] for d in cur.description] if cur.description else []
    except Exception as exc:
        raise _Retryable(f"sqlite error: {exc}") from exc
    finally:
        await db.close()
    return cols, [list(r) for r in rows]


# ---- Helpers ---------------------------------------------------------------


def _normalise_row(row: list[Any]) -> list[Any]:
    """Make rows JSON-serialisable. Coerce datetimes / decimals / bytes."""
    return [_normalise_value(v) for v in row]


def _normalise_value(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, (_dt.date, _dt.datetime, _dt.time)):
        return v.isoformat()
    if isinstance(v, _dt.timedelta):
        return v.total_seconds()
    # Decimal, UUID, IP-address, etc.
    try:
        return str(v)
    except Exception:
        return repr(v)


def _parse_mysql_dsn(dsn: str) -> dict:
    from urllib.parse import urlparse

    u = urlparse(dsn if "://" in dsn else f"mysql://{dsn}")
    return {
        "host": u.hostname or "127.0.0.1",
        "port": u.port or 3306,
        "user": u.username or "",
        "password": u.password or "",
        "database": (u.path or "/").lstrip("/"),
    }


def _timeout_left() -> float:
    """Default per-call timeout; consumed by asyncpg/aiomysql."""
    return float(Settings().tool_timeout_sec)


class _Retryable(QueryError):
    """Internal sentinel — caught at the top of `run` and re-raised as QueryError."""
