"""db.schema — list tables / columns / types for a connection.

Strategy by dialect:
    - Postgres  →  information_schema.columns joined with information_schema.tables
    - MySQL     →  information_schema.columns (DATABASE() scoped)
    - SQLite    →  pragma table_list + pragma table_info
    - Others    →  empty list (placeholder)

Returns:
    {
      "ok": True,
      "tables": [
        {
          "schema": "public",
          "name": "orders",
          "kind": "BASE TABLE",
          "columns": [
            {"name": "id", "type": "integer", "nullable": False, "default": None},
            ...
          ],
        },
        ...
      ]
    }
"""

from __future__ import annotations

import asyncio
import time

from mcp_server_database.config import Settings
from mcp_server_database.connections import Connections
from mcp_server_database.safety.dialect_allowlist import dialect_from_connection


class SchemaError(Exception):
    pass


# ---- Public entry point ----------------------------------------------------


async def run(args: dict) -> dict:
    started = time.monotonic()
    connection = args["connection"]
    conns = Connections(Settings())
    dsn = conns.dsn(connection)
    dialect = dialect_from_connection(connection)
    timeout_sec = Settings().tool_timeout_sec

    try:
        tables = await asyncio.wait_for(
            _introspect(dsn, dialect),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError as exc:
        raise SchemaError(f"schema introspection exceeded {timeout_sec}s timeout") from exc
    except Exception as exc:
        raise SchemaError(f"schema failed: {exc}") from exc

    return {
        "ok": True,
        "dialect": dialect,
        "connection": connection,
        "tables": tables,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


# ---- Per-dialect implementation -------------------------------------------


async def _introspect(dsn: str, dialect: str) -> list[dict]:
    if dialect in {"postgres", "redshift"}:
        return await _pg(dsn)
    if dialect == "mysql":
        return await _my(dsn)
    if dialect == "sqlite":
        return await _sq(dsn)
    raise SchemaError(f"schema introspection unsupported for dialect: {dialect}")


_PG_QUERY = """
SELECT
    table_schema,
    table_name,
    table_type
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
  AND table_type IN ('BASE TABLE', 'VIEW')
ORDER BY table_schema, table_name;
"""


async def _pg(dsn: str) -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        tables = await conn.fetch(_PG_QUERY)
        out: list[dict] = []
        for t in tables:
            schema, name = t["table_schema"], t["table_name"]
            cols = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
                """,
                schema,
                name,
            )
            out.append(
                {
                    "schema": schema,
                    "name": name,
                    "kind": t["table_type"],
                    "columns": [
                        {
                            "name": c["column_name"],
                            "type": c["data_type"],
                            "nullable": c["is_nullable"].upper() == "YES",
                            "default": c["column_default"],
                        }
                        for c in cols
                    ],
                }
            )
        return out
    finally:
        await conn.close()


_MY_QUERY = """
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
FROM information_schema.tables
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_SCHEMA, TABLE_NAME;
"""

_MY_COL_QUERY = """
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM information_schema.columns
WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
ORDER BY ORDINAL_POSITION;
"""


async def _my(dsn: str) -> list[dict]:
    from urllib.parse import urlparse

    import aiomysql

    u = urlparse(dsn if "://" in dsn else f"mysql://{dsn}")
    conn = await aiomysql.connect(
        host=u.hostname or "127.0.0.1",
        port=u.port or 3306,
        user=u.username or "",
        password=u.password or "",
        db=(u.path or "/").lstrip("/"),
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(_MY_QUERY)
            tables = await cur.fetchall()
            out: list[dict] = []
            for schema, name, kind in tables:
                await cur.execute(_MY_COL_QUERY, (schema, name))
                cols = await cur.fetchall()
                out.append(
                    {
                        "schema": schema,
                        "name": name,
                        "kind": kind,
                        "columns": [
                            {
                                "name": c[0],
                                "type": c[1],
                                "nullable": c[2].upper() == "YES",
                                "default": c[3],
                            }
                            for c in cols
                        ],
                    }
                )
            return out
    finally:
        conn.close()


async def _sq(dsn: str) -> list[dict]:
    import aiosqlite

    path = dsn.split(":///", 1)[-1].split("?", 1)[0]
    db = await aiosqlite.connect(path)
    try:
        cur = await db.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        rows = await cur.fetchall()
        out: list[dict] = []
        for name, kind in rows:
            ccur = await db.execute(f"PRAGMA table_info({name})")
            cols = await ccur.fetchall()
            # SQLite quirk: PRIMARY KEY columns don't have notnull=1 unless
            # they were also declared NOT NULL. Reconcile by treating pk>0
            # as implicitly NOT NULL (per SQL standard).
            out.append(
                {
                    "schema": "main",
                    "name": name,
                    "kind": kind.upper(),
                    "columns": [
                        {
                            "name": c[1],
                            "type": c[2],
                            # c[3]=notnull flag, c[5]=pk ordinal (0 if not PK)
                            "nullable": not (bool(c[3]) or int(c[5] or 0) > 0),
                            "default": c[4],
                            "primary_key": int(c[5] or 0) > 0,
                        }
                        for c in cols
                    ],
                }
            )
        return out
    finally:
        await db.close()
