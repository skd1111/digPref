"""Phase 16 · thinking_steps SQLite 存储（trace.db 物理隔离）。

表结构与规划文档一致：id / session_id / message_id / step_index / node_name /
thinking / thinking_tokens / tool_calls(JSON) / file_operations(JSON) /
decision / tokens_used / latency_ms / created_at。

只追加不删改（金融合规审计红线，架构师忠告 6）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import aiosqlite

from agent.config import settings
from agent.trace.models import ThinkingStep

logger = logging.getLogger(__name__)

_LOCK = asyncio.Lock()

SCHEMA_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS thinking_steps (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    message_id      TEXT,
    step_index      INTEGER NOT NULL,
    node_name       TEXT NOT NULL,
    thinking        TEXT,
    thinking_tokens INTEGER,
    tool_calls      TEXT,
    file_operations TEXT,
    decision        TEXT,
    tokens_used     INTEGER,
    latency_ms      INTEGER,
    created_at      INTEGER
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_thinking_steps_session ON thinking_steps(session_id, step_index);
CREATE INDEX IF NOT EXISTS idx_thinking_steps_created ON thinking_steps(created_at);
"""


def _db_target(db_path: str | None = None) -> str:
    return db_path or settings.trace_db_path


async def insert_step(step: ThinkingStep, db_path: str | None = None) -> None:
    """追加一条思维链步骤（幂等：同 id 重复插入忽略）。"""
    target = _db_target(db_path)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    async with _LOCK, aiosqlite.connect(target) as db:
        await db.executescript(SCHEMA_CREATE_TABLE + SCHEMA_INDEXES)
        await db.execute(
            "INSERT OR IGNORE INTO thinking_steps("
            "  id, session_id, message_id, step_index, node_name,"
            "  thinking, thinking_tokens, tool_calls, file_operations,"
            "  decision, tokens_used, latency_ms, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                step.id,
                step.session_id,
                step.message_id,
                step.step_index,
                step.node_name,
                step.thinking,
                step.thinking_tokens,
                json.dumps(step.tool_calls, ensure_ascii=False, default=str),
                json.dumps(
                    [op.to_dict() for op in step.file_operations],
                    ensure_ascii=False,
                    default=str,
                ),
                step.decision,
                step.tokens_used,
                step.latency_ms,
                step.created_at,
            ),
        )
        await db.commit()


async def list_steps(
    session_id: str,
    limit: int = 500,
    offset: int = 0,
    db_path: str | None = None,
) -> list[ThinkingStep]:
    """按 step_index 升序返回会话的全部思维链步骤。"""
    target = _db_target(db_path)
    if not Path(target).exists():
        return []
    async with aiosqlite.connect(target) as db:
        await db.executescript(SCHEMA_CREATE_TABLE)
        cur = await db.execute(
            "SELECT id, session_id, message_id, step_index, node_name, thinking,"
            "       thinking_tokens, tool_calls, file_operations, decision,"
            "       tokens_used, latency_ms, created_at "
            "FROM thinking_steps WHERE session_id = ? "
            "ORDER BY step_index ASC, created_at ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        )
        rows = await cur.fetchall()
    return [_row_to_step(r) for r in rows]


async def get_step(step_id: str, db_path: str | None = None) -> ThinkingStep | None:
    target = _db_target(db_path)
    if not Path(target).exists():
        return None
    async with aiosqlite.connect(target) as db:
        await db.executescript(SCHEMA_CREATE_TABLE)
        cur = await db.execute(
            "SELECT id, session_id, message_id, step_index, node_name, thinking,"
            "       thinking_tokens, tool_calls, file_operations, decision,"
            "       tokens_used, latency_ms, created_at "
            "FROM thinking_steps WHERE id = ?",
            (step_id,),
        )
        row = await cur.fetchone()
    return _row_to_step(row) if row else None


async def count_steps(session_id: str, db_path: str | None = None) -> int:
    """会话已有步骤数（collector 分配 step_index 用）。"""
    target = _db_target(db_path)
    if not Path(target).exists():
        return 0
    async with aiosqlite.connect(target) as db:
        await db.executescript(SCHEMA_CREATE_TABLE)
        cur = await db.execute(
            "SELECT COUNT(*) FROM thinking_steps WHERE session_id = ?",
            (session_id,),
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def recent_sessions(limit: int = 20, db_path: str | None = None) -> list[dict]:
    """按最近活动时间倒序返回会话列表（前端启动时加载最近会话思维链用）。"""
    target = _db_target(db_path)
    if not Path(target).exists():
        return []
    async with aiosqlite.connect(target) as db:
        await db.executescript(SCHEMA_CREATE_TABLE)
        cur = await db.execute(
            "SELECT session_id, COUNT(*) AS steps, MAX(created_at) AS last_ts "
            "FROM thinking_steps GROUP BY session_id "
            "ORDER BY last_ts DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
    return [
        {"session_id": r[0], "steps": int(r[1]), "last_ts": int(r[2] or 0)}
        for r in rows
    ]


def _row_to_step(row: tuple) -> ThinkingStep:
    def _json(text: str | None) -> list:
        if not text:
            return []
        try:
            v = json.loads(text)
            return v if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []

    return ThinkingStep.from_dict(
        {
            "id": row[0],
            "session_id": row[1],
            "message_id": row[2],
            "step_index": row[3],
            "node_name": row[4],
            "thinking": row[5],
            "thinking_tokens": row[6],
            "tool_calls": _json(row[7]),
            "file_operations": _json(row[8]),
            "decision": row[9],
            "tokens_used": row[10],
            "latency_ms": row[11],
            "created_at": row[12] or 0,
        }
    )
