"""evolution.storage —— evolution.db 持久层（与其他 db 物理隔离，设计文档 §6）。

写入用 aiosqlite（异步，与 trace/storage.py 同范式）；经验注入检索用
同步 sqlite3（`_merge_extra_rules` 是同步通道，且只读小查询，与
llm/router.py::load_enabled_local_backend 同风格）。

红线：所有写入均为 best-effort，调用方负责 try/except 不阻塞主链路。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from typing import Any

import aiosqlite

from agent.config import settings

logger = logging.getLogger(__name__)

_LOCK = asyncio.Lock()

SCHEMA_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS evaluation_signals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    message_id     TEXT,
    task_signature TEXT NOT NULL,
    source         TEXT NOT NULL,
    score          REAL,
    rating         INTEGER,
    correction     TEXT,
    reason         TEXT,
    ts             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trajectories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    task_signature TEXT NOT NULL,
    intent_json    TEXT NOT NULL DEFAULT '{}',
    active_skill_id TEXT,
    tool_fp        TEXT,
    outcome        TEXT NOT NULL,
    answer_digest  TEXT,
    ts             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiences (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    insight        TEXT NOT NULL,
    tags_json      TEXT NOT NULL DEFAULT '[]',
    applies_to     TEXT,
    source_session TEXT,
    attribution    TEXT,
    hit_count      INTEGER NOT NULL DEFAULT 0,
    score          REAL NOT NULL DEFAULT 0.5,
    status         TEXT NOT NULL DEFAULT 'active',
    ts             TEXT NOT NULL
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_sig_signature ON evaluation_signals(task_signature, ts);
CREATE INDEX IF NOT EXISTS idx_sig_source    ON evaluation_signals(source);
CREATE INDEX IF NOT EXISTS idx_traj_signature ON trajectories(task_signature, ts);
"""


def _db_target(db_path: str | None = None) -> str:
    return db_path or settings.evolution_db_path


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA_CREATE_TABLES + SCHEMA_INDEXES)


# ---- 评测信号 ------------------------------------------------------------


async def record_signal(
    *,
    session_id: str,
    task_signature: str,
    source: str,
    score: float | None = None,
    rating: int | None = None,
    correction: str = "",
    reason: str = "",
    message_id: str = "",
    db_path: str | None = None,
) -> None:
    """追加一条评测信号（env / judge / user 三路归一）。"""
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        await conn.execute(
            "INSERT INTO evaluation_signals"
            " (session_id, message_id, task_signature, source, score, rating,"
            "  correction, reason, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                message_id or None,
                task_signature,
                source,
                score,
                rating,
                correction or None,
                reason or None,
                _now_iso(),
            ),
        )
        await conn.commit()


# ---- 轨迹 ----------------------------------------------------------------


async def record_trajectory(
    *,
    session_id: str,
    task_signature: str,
    intent: dict[str, Any],
    active_skill_id: str,
    tool_fp: str,
    outcome: str,
    answer_digest: str,
    db_path: str | None = None,
) -> int:
    """追加一条任务轨迹摘要（不含参数明文 / 凭证）。返回行 id。"""
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "INSERT INTO trajectories"
            " (session_id, task_signature, intent_json, active_skill_id,"
            "  tool_fp, outcome, answer_digest, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                task_signature,
                json.dumps(intent, ensure_ascii=False, default=str),
                active_skill_id or None,
                tool_fp or None,
                outcome,
                answer_digest or None,
                _now_iso(),
            ),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)


async def get_trajectory(trajectory_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    """按 id 读轨迹（用户 👎 反馈触发反思时用）。"""
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT id, session_id, task_signature, intent_json, active_skill_id,"
            " tool_fp, outcome, answer_digest, ts FROM trajectories WHERE id = ?",
            (trajectory_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "session_id": row[1],
        "task_signature": row[2],
        "intent_json": row[3],
        "active_skill_id": row[4] or "",
        "tool_fp": row[5] or "",
        "outcome": row[6],
        "answer_digest": row[7] or "",
        "ts": row[8],
    }


async def latest_trajectory_by_session(
    session_id: str, db_path: str | None = None
) -> dict[str, Any] | None:
    """会话最近一条轨迹（反馈未携带 trajectoryId 时的兜底定位）。"""
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT id FROM trajectories WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return await get_trajectory(int(row[0]), db_path)


# ---- 经验库（写侧，异步） --------------------------------------------------


async def insert_experience(
    *,
    insight: str,
    tags: list[str],
    applies_to: str = "",
    source_session: str = "",
    attribution: str = "",
    db_path: str | None = None,
) -> int:
    """新增一条经验（默认 active，置信 0.5）。返回行 id。"""
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "INSERT INTO experiences"
            " (insight, tags_json, applies_to, source_session, attribution, ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                insight.strip()[:500],
                json.dumps(tags[:8], ensure_ascii=False),
                applies_to or None,
                source_session or None,
                attribution or None,
                _now_iso(),
            ),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)


async def list_experiences(
    *, include_disabled: bool = True, db_path: str | None = None
) -> list[dict[str, Any]]:
    """经验库列表（管理页用；按置信倒序）。"""
    status_clause = "" if include_disabled else " WHERE status = 'active'"
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT id, insight, tags_json, applies_to, source_session,"
            f" attribution, hit_count, score, status, ts FROM experiences{status_clause}"
            " ORDER BY score DESC, hit_count DESC, id DESC"
        )
        rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            tags = json.loads(r[2]) if r[2] else []
        except (TypeError, ValueError):
            tags = []
        out.append(
            {
                "id": r[0],
                "insight": r[1],
                "tags": tags if isinstance(tags, list) else [],
                "applies_to": r[3] or "",
                "source_session": r[4] or "",
                "attribution": r[5] or "",
                "hit_count": r[6],
                "score": r[7],
                "status": r[8],
                "ts": r[9],
            }
        )
    return out


async def set_experience_status(
    experience_id: int, status: str, db_path: str | None = None
) -> bool:
    """启停经验（管理页人工干预）。返回是否有行被更新。"""
    if status not in ("active", "disabled"):
        raise ValueError(f"invalid experience status: {status}")
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "UPDATE experiences SET status = ? WHERE id = ?", (status, experience_id)
        )
        await conn.commit()
        return cur.rowcount > 0


async def delete_experience(experience_id: int, db_path: str | None = None) -> bool:
    """删除经验（管理页人工干预）。"""
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute("DELETE FROM experiences WHERE id = ?", (experience_id,))
        await conn.commit()
        return cur.rowcount > 0


# ---- 经验库（读侧，同步 —— extra_rules 注入通道）---------------------------


def retrieve_experiences_sync(
    intent_category: str,
    skill_id: str,
    *,
    top_k: int | None = None,
    max_chars: int | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """同步检索 top-k 条可用经验（注入提示词用；失败返空不抛）。

    排序：签名特征精确匹配（intent 细分类型 / skill id）优先，
    其次按置信分与命中次数。通用经验（applies_to 为空）作兜底。
    """
    k = top_k if top_k is not None else settings.evolution_experience_top_k
    try:
        conn = sqlite3.connect(_db_target(db_path), timeout=2)
        try:
            conn.executescript(SCHEMA_CREATE_TABLES)
            cur = conn.execute(
                "SELECT id, insight, applies_to FROM experiences"
                " WHERE status = 'active'"
                " ORDER BY (CASE WHEN applies_to IN (?, ?) THEN 1 ELSE 0 END) DESC,"
                " score DESC, hit_count DESC, id DESC LIMIT ?",
                (intent_category or "", skill_id or "", max(1, k)),
            )
            rows = cur.fetchall()
            hit_ids = [r[0] for r in rows]
            if hit_ids:
                placeholders = ",".join("?" * len(hit_ids))
                conn.execute(
                    f"UPDATE experiences SET hit_count = hit_count + 1 WHERE id IN ({placeholders})",
                    hit_ids,
                )
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # best-effort：检索失败不阻塞任务执行
        logger.warning("[evolution] experience retrieval failed: %s", exc)
        return []
    return [{"id": r[0], "insight": r[1], "applies_to": r[2] or ""} for r in rows]


def format_experience_snippet(
    experiences: list[dict[str, Any]], *, max_chars: int | None = None
) -> str:
    """把经验列表拼成注入片段（带字符上限，设计文档 §3.3）。"""
    if not experiences:
        return ""
    limit = max_chars if max_chars is not None else settings.evolution_experience_max_chars
    lines = ["【历史经验（来自以往任务的反思总结，供参考）】"]
    for i, exp in enumerate(experiences, 1):
        lines.append(f"{i}. {str(exp.get('insight') or '')[:200]}")
    text = "\n".join(lines)
    return text[:limit]
