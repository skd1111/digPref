"""MCP stdio entry — registers tools and starts the server.

Dispatching order (top-down):
    1. Schema / query / execute tool resolution.
    2. **SQL validation pipeline** — runs for both query and execute:
       - dialect allowlist
       - sqlglot parse + multi-statement + dangerous-function scan
       - dangerous-ops hard bans (DDL, no-WHERE writes, etc.)
    3. **Read-only enforcement** at the connection level.
    4. **Hard timeout** enforced by asyncio.wait_for.
    5. **Audit emission** — every call (success or failure) is appended.
    6. **Error mapping** — every internal exception type maps to a stable
       JSON shape so the Agent / Auto-Repair can pattern-match on `code`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool

from mcp_server_database.audit.emitter import audit
from mcp_server_database.config import Settings
from mcp_server_database.safety.dangerous_ops import (
    DestructiveOpError,
    assert_no_destructive,
)
from mcp_server_database.safety.dialect_allowlist import (
    UnsupportedDialectError,
    dialect_from_connection,
)
from mcp_server_database.safety.readonly_enforce import ReadOnlyViolationError
from mcp_server_database.safety.sqlglot_validator import (
    UnsafeSqlError,
    assert_safe_sql,
)
from mcp_server_database.tools import execute, query, schema
from mcp_server_database.tools.execute import ApprovalMissingError, ExecuteError
from mcp_server_database.tools.query import QueryError
from mcp_server_database.tools.schema import SchemaError

log = logging.getLogger("mcp-server-database")
server = Server("mcp-server-database")
settings = Settings()


# ---- Tool schemas ----------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="db.query",
            description="Execute a read-only SQL query. Returns rows (truncated).",
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "connection": {
                        "type": "string",
                        "description": "Logical connection name (e.g. 'orders_pg').",
                    },
                    "sql": {"type": "string", "minLength": 1},
                    "params": {"type": "array", "items": {}},
                    "_row_limit": {"type": "integer", "minimum": 1, "maximum": 10000},
                },
                "required": ["connection", "sql"],
            },
        ),
        Tool(
            name="db.execute",
            description=(
                "Execute a write statement. **Requires HITL approval** "
                "— pass the `approval_id` issued by the upstream LangGraph gate."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "connection": {"type": "string"},
                    "sql": {"type": "string", "minLength": 1},
                    "params": {"type": "array", "items": {}},
                    "approval_id": {"type": "string", "minLength": 1},
                },
                "required": ["connection", "sql", "approval_id"],
            },
        ),
        Tool(
            name="db.schema",
            description="List tables / columns / types for a connection.",
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "connection": {"type": "string"},
                },
                "required": ["connection"],
            },
        ),
    ]


# ---- Dispatcher ------------------------------------------------------------


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> dict:
    """Single entry point that handles every tool + every error path."""
    started = time.monotonic()
    audit("db.tool.call", {"name": name, "connection": arguments.get("connection")})
    try:
        # Pre-validate SQL for any tool that carries it
        if "sql" in arguments:
            _prevalidate_sql(arguments)

        if name == "db.query":
            result = await query.run(arguments)
        elif name == "db.execute":
            result = await execute.run(arguments)
        elif name == "db.schema":
            result = await schema.run(arguments)
        else:
            raise ValueError(f"unknown tool: {name}")

        result = _attach_meta(result, started, name)
        audit("db.tool.ok", {"name": name, **result})
        return result

    except _Handled as exc:
        audit("db.tool.reject", {"name": name, "code": exc.code, "message": exc.message})
        return _error_response(exc.code, exc.message, started, name)
    except asyncio.TimeoutError as exc:
        audit("db.tool.timeout", {"name": name, "error": str(exc)})
        return _error_response(
            "TIMEOUT", f"tool exceeded {settings.tool_timeout_sec}s", started, name
        )
    except Exception as exc:
        handled = _map_exception(exc)
        audit(
            "db.tool.error",
            {
                "name": name,
                "code": handled.code,
                "message": handled.message,
                "type": type(exc).__name__,
            },
        )
        log.exception("unhandled error in %s", name)
        return _error_response(handled.code, handled.message, started, name)


# ---- Helpers ---------------------------------------------------------------


def _prevalidate_sql(arguments: dict) -> None:
    """Run the cheapest, fail-fast checks before dispatching to the tool."""
    sql = arguments.get("sql") or ""
    connection = arguments.get("connection") or ""
    if not sql.strip():
        raise UnsafeSqlError("sql is empty")
    if not connection:
        raise UnsafeSqlError("connection is required")
    dialect = dialect_from_connection(connection)
    assert_safe_sql(sql, dialect=dialect)
    # Belt-and-braces: db.execute also runs this in execute.run,
    # but rejecting here lets the LLM get a faster error signal.
    if "approval_id" in arguments:
        assert_no_destructive(sql, dialect=dialect)


def _attach_meta(result: dict, started: float, name: str) -> dict:
    result.setdefault("tool", name)
    result.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
    return result


def _error_response(code: str, message: str, started: float, name: str) -> dict:
    return {
        "ok": False,
        "tool": name,
        "code": code,
        "message": message,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


# ---- Exception → ErrorCode mapping ----------------------------------------


class _Handled(Exception):
    code: str = "ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _map_exception(exc: Exception) -> _Handled:
    """Translate any internal exception into a stable (code, message) shape."""
    if isinstance(exc, UnsafeSqlError):
        return _Handled(str(exc))._replace_code("UNSAFE_SQL")  # type: ignore[attr-defined]
    if isinstance(exc, DestructiveOpError):
        return _Handled(str(exc))._replace_code("DESTRUCTIVE_OP")  # type: ignore[attr-defined]
    if isinstance(exc, ReadOnlyViolationError):
        return _Handled(str(exc))._replace_code("READONLY_VIOLATION")  # type: ignore[attr-defined]
    if isinstance(exc, ApprovalMissingError):
        return _Handled(str(exc))._replace_code("APPROVAL_MISSING")  # type: ignore[attr-defined]
    if isinstance(exc, UnsupportedDialectError):
        return _Handled(str(exc))._replace_code("UNSUPPORTED_DIALECT")  # type: ignore[attr-defined]
    if isinstance(exc, QueryError):
        return _Handled(str(exc))._replace_code("DRIVER_ERROR")  # type: ignore[attr-defined]
    if isinstance(exc, ExecuteError):
        return _Handled(str(exc))._replace_code("DRIVER_ERROR")  # type: ignore[attr-defined]
    if isinstance(exc, SchemaError):
        return _Handled(str(exc))._replace_code("SCHEMA_ERROR")  # type: ignore[attr-defined]
    return _Handled(str(exc))


def _handled_replace_code(self, code: str) -> _Handled:
    """Replace .code in place and return self."""
    self.__class__ = type(self.__class__.__name__, (self.__class__,), {"code": code})
    return self


# Monkey-patch _Handled to support _replace_code
_Handled._replace_code = _handled_replace_code  # type: ignore[attr-defined]


# ---- Entry point -----------------------------------------------------------


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("EAIDE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
