"""Trace persistence — append every LangGraph node transition to SQLite.

Two consumers:
    1. The trace pane in the desktop UI (queries the SQLite directly).
    2. LangSmith export (best-effort, opt-in via env var).

Schema is intentionally simple:
    CREATE TABLE trace (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id       TEXT NOT NULL,
        thread_id    TEXT,
        node         TEXT NOT NULL,
        status       TEXT NOT NULL,            -- running | ok | fail | skipped
        ts           TEXT NOT NULL,            -- RFC 3339
        duration_ms  INTEGER,
        summary      TEXT,
        error        TEXT,
        approval_id  TEXT,
        decision     TEXT,                     -- approve | reject
        attempts     INTEGER,
        rationale    TEXT,
        tool_name    TEXT
    );
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent.config import settings
from agent.observability.langsmith import enabled as langsmith_enabled

log = logging.getLogger(__name__)
_LOCK = asyncio.Lock()


SCHEMA = """
CREATE TABLE IF NOT EXISTS trace (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    thread_id    TEXT,
    node         TEXT NOT NULL,
    status       TEXT NOT NULL,
    ts           TEXT NOT NULL,
    duration_ms  INTEGER,
    summary      TEXT,
    error        TEXT,
    approval_id  TEXT,
    decision     TEXT,
    attempts     INTEGER,
    rationale    TEXT,
    tool_name    TEXT
);
CREATE INDEX IF NOT EXISTS idx_trace_run ON trace(run_id);
CREATE INDEX IF NOT EXISTS idx_trace_ts ON trace(ts);
CREATE INDEX IF NOT EXISTS idx_trace_node ON trace(node);
"""


# ---- Public API ------------------------------------------------------------


async def record(node: str, status: str, *, run_id: str, **meta: Any) -> None:
    """Append one trace row. Fire-and-forget."""
    Path(settings.audit_db_path).parent.mkdir(parents=True, exist_ok=True)
    row = {
        "run_id": run_id,
        "node": node,
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
        "thread_id": meta.get("thread_id"),
        "duration_ms": meta.get("duration_ms"),
        "summary": meta.get("summary"),
        "error": meta.get("error"),
        "approval_id": meta.get("approval_id"),
        "decision": meta.get("decision"),
        "attempts": meta.get("attempts"),
        "rationale": meta.get("rationale"),
        "tool_name": meta.get("tool_name"),
    }
    try:
        async with _LOCK, aiosqlite.connect(settings.audit_db_path) as db:
            await db.executescript(SCHEMA)
            await db.execute(
                """
                INSERT INTO trace (
                    run_id, thread_id, node, status, ts, duration_ms,
                    summary, error, approval_id, decision, attempts, rationale, tool_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["run_id"],
                    row["thread_id"],
                    row["node"],
                    row["status"],
                    row["ts"],
                    row["duration_ms"],
                    row["summary"],
                    row["error"],
                    row["approval_id"],
                    row["decision"],
                    row["attempts"],
                    row["rationale"],
                    row["tool_name"],
                ),
            )
            await db.commit()
    except Exception as exc:
        log.warning("trace persistence failed: %s", exc)

    # Best-effort LangSmith export
    if langsmith_enabled():
        await _export_to_langsmith(row)


async def query_run(run_id: str) -> list[dict]:
    async with aiosqlite.connect(settings.audit_db_path) as db:
        cur = await db.execute(
            "SELECT node, status, ts, duration_ms, summary, error, "
            "approval_id, decision, attempts, rationale, tool_name "
            "FROM trace WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        )
        rows = await cur.fetchall()
    return [
        {
            "node": r[0],
            "status": r[1],
            "ts": r[2],
            "duration_ms": r[3],
            "summary": r[4],
            "error": r[5],
            "approval_id": r[6],
            "decision": r[7],
            "attempts": r[8],
            "rationale": r[9],
            "tool_name": r[10],
        }
        for r in rows
    ]


async def stream_all_runs() -> AsyncIterator[dict]:
    """Yield all runs, newest first. Used by the UI's run-picker."""
    async with aiosqlite.connect(settings.audit_db_path) as db:
        cur = await db.execute(
            "SELECT run_id, MIN(ts), MAX(ts), COUNT(*) "
            "FROM trace GROUP BY run_id ORDER BY MIN(ts) DESC LIMIT 200"
        )
        rows = await cur.fetchall()
    for run_id, started, ended, count in rows:
        yield {
            "run_id": run_id,
            "started_at": started,
            "ended_at": ended,
            "step_count": count,
        }


# ---- LangSmith export ------------------------------------------------------


async def _export_to_langsmith(row: dict) -> None:
    """Best-effort POST to LangSmith. Failure is silently ignored."""
    import os

    import httpx

    endpoint = os.environ.get("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
    api_key = os.environ.get("LANGCHAIN_API_KEY", "")
    if not api_key:
        return
    payload = {
        "id": f"eaide-{row['run_id']}-{row['node']}-{int(_ts(row['ts']))}",
        "name": row["node"],
        "run_type": "chain",
        "start_time": _ts(row["ts"]),
        "end_time": _ts(row["ts"]) + (row.get("duration_ms") or 0) / 1000,
        "status": "success" if row["status"] == "ok" else "error",
        "extra": {
            "eaide_run_id": row["run_id"],
            "eaide_node": row["node"],
            "eaide_status": row["status"],
        },
        "error": row.get("error"),
    }
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(
                f"{endpoint}/runs",
                json=payload,
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                },
            )
    except Exception:
        pass


def _ts(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso).timestamp()
