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
import os
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
-- Phase 19 V1：技能蒸馏草稿（默认不启用，人工审核后转正式 Skill）
CREATE TABLE IF NOT EXISTS skill_drafts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slug           TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    yaml_text      TEXT NOT NULL,
    task_signature TEXT,
    status         TEXT NOT NULL DEFAULT 'draft',
    source_session TEXT,
    ts             TEXT NOT NULL
);
-- Phase 19 V1.5 预留：Prompt 版本与优化实验（建表先行，免后续迁移）
CREATE TABLE IF NOT EXISTS prompt_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id      TEXT NOT NULL,
    version       INTEGER NOT NULL,
    few_shot_json TEXT NOT NULL,
    gain          REAL,
    status        TEXT NOT NULL DEFAULT 'candidate',
    ts            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    task_signature TEXT,
    status         TEXT NOT NULL,
    detail_json    TEXT,
    ts             TEXT NOT NULL
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_sig_signature ON evaluation_signals(task_signature, ts);
CREATE INDEX IF NOT EXISTS idx_sig_source    ON evaluation_signals(source);
CREATE INDEX IF NOT EXISTS idx_traj_signature ON trajectories(task_signature, ts);
CREATE INDEX IF NOT EXISTS idx_draft_status  ON skill_drafts(status, ts);
CREATE INDEX IF NOT EXISTS idx_pv_skill      ON prompt_versions(skill_id, version);
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


# ---- Prompt 版本（V1.5） ---------------------------------------------------


async def low_score_feedback(
    task_signature: str, *, limit: int = 5, db_path: str | None = None
) -> list[str]:
    """同签名低分反馈文本（用户 👎 纠错 / Judge 理由），供 Prompt 优化取材。"""
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT COALESCE(NULLIF(correction, ''), reason) FROM evaluation_signals"
            " WHERE task_signature = ? AND (rating = 0 OR (rating IS NOT NULL AND rating <= 2))"
            "   AND COALESCE(NULLIF(correction, ''), reason) IS NOT NULL"
            " ORDER BY id DESC LIMIT ?",
            (task_signature, max(1, limit)),
        )
        rows = await cur.fetchall()
    return [str(r[0])[:200] for r in rows if r[0]]


async def record_experiment_run(
    *,
    kind: str,
    task_signature: str = "",
    status: str,
    detail: dict[str, Any] | None = None,
    db_path: str | None = None,
) -> None:
    """记录一次优化实验运行（审计 + 看板）。"""
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        await conn.execute(
            "INSERT INTO experiment_runs (kind, task_signature, status, detail_json, ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                kind,
                task_signature or None,
                status,
                json.dumps(detail or {}, ensure_ascii=False, default=str),
                _now_iso(),
            ),
        )
        await conn.commit()


async def insert_prompt_version(
    *,
    skill_id: str,
    few_shot: list[dict[str, str]],
    gain: float | None = None,
    status: str = "candidate",
    db_path: str | None = None,
) -> int:
    """新增 Prompt 版本（版本号 = 该 skill 现有最大版本 + 1）。返回行 id。"""
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT MAX(version) FROM prompt_versions WHERE skill_id = ?", (skill_id,)
        )
        row = await cur.fetchone()
        next_version = int(row[0] or 0) + 1 if row else 1
        cur = await conn.execute(
            "INSERT INTO prompt_versions (skill_id, version, few_shot_json, gain, status, ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                skill_id,
                next_version,
                json.dumps(few_shot, ensure_ascii=False),
                gain,
                status,
                _now_iso(),
            ),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)


async def list_prompt_versions(
    skill_id: str | None = None, db_path: str | None = None
) -> list[dict[str, Any]]:
    """Prompt 版本列表（可按 skill 过滤；版本倒序）。"""
    clause = " WHERE skill_id = ?" if skill_id else ""
    args: tuple[str, ...] = (skill_id,) if skill_id else ()
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT id, skill_id, version, few_shot_json, gain, status, ts"
            f" FROM prompt_versions{clause} ORDER BY skill_id, version DESC",
            args,
        )
        rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            few_shot = json.loads(r[3]) if r[3] else []
        except (TypeError, ValueError):
            few_shot = []
        out.append(
            {
                "id": r[0],
                "skill_id": r[1],
                "version": r[2],
                "few_shot": few_shot if isinstance(few_shot, list) else [],
                "gain": r[4],
                "status": r[5],
                "ts": r[6],
            }
        )
    return out


async def get_prompt_version(version_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    items = await list_prompt_versions(db_path=db_path)
    return next((it for it in items if it["id"] == version_id), None)


async def set_prompt_version_status(
    version_id: int, status: str, db_path: str | None = None
) -> bool:
    if status not in ("candidate", "active", "rolled_back"):
        raise ValueError(f"invalid prompt version status: {status}")
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "UPDATE prompt_versions SET status = ? WHERE id = ?", (status, version_id)
        )
        await conn.commit()
        return cur.rowcount > 0


# ---- 技能草稿（V1，异步） ---------------------------------------------------


async def insert_skill_draft(
    *,
    slug: str,
    name: str,
    yaml_text: str,
    task_signature: str = "",
    source_session: str = "",
    db_path: str | None = None,
) -> int:
    """新增技能蒸馏草稿（status=draft，永不自动启用）。返回行 id。"""
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "INSERT INTO skill_drafts"
            " (slug, name, yaml_text, task_signature, source_session, ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                slug,
                name,
                yaml_text,
                task_signature or None,
                source_session or None,
                _now_iso(),
            ),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)


async def list_skill_drafts(
    *, status: str = "draft", db_path: str | None = None
) -> list[dict[str, Any]]:
    """草稿列表（默认只看待审）。"""
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT id, slug, name, yaml_text, task_signature, status, source_session, ts"
            " FROM skill_drafts WHERE status = ? ORDER BY id DESC",
            (status,),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "slug": r[1],
            "name": r[2],
            "yaml_text": r[3],
            "task_signature": r[4] or "",
            "status": r[5],
            "source_session": r[6] or "",
            "ts": r[7],
        }
        for r in rows
    ]


async def get_skill_draft(draft_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT id, slug, name, yaml_text, task_signature, status, source_session, ts"
            " FROM skill_drafts WHERE id = ?",
            (draft_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "slug": row[1],
        "name": row[2],
        "yaml_text": row[3],
        "task_signature": row[4] or "",
        "status": row[5],
        "source_session": row[6] or "",
        "ts": row[7],
    }


async def set_skill_draft_status(draft_id: int, status: str, db_path: str | None = None) -> bool:
    """草稿状态流转（draft → approved / rejected）。"""
    if status not in ("draft", "approved", "rejected"):
        raise ValueError(f"invalid skill draft status: {status}")
    async with _LOCK, aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "UPDATE skill_drafts SET status = ? WHERE id = ?", (status, draft_id)
        )
        await conn.commit()
        return cur.rowcount > 0


async def has_draft_for_signature(task_signature: str, db_path: str | None = None) -> bool:
    """同签名是否已有未拒草稿或已采纳草稿（防重复蒸馏；rejected 不拦，允许改进后再试）。

    approved 一并拦截：新技能被采纳后、带 active_skill_id 的轨迹产生前的窗口期内，
    不能对同签名再蒸馏出内容雷同的草稿（「已有技能承接」检查只看最近 5 条轨迹）。
    """
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT 1 FROM skill_drafts WHERE task_signature = ?"
            " AND status IN ('draft', 'approved') LIMIT 1",
            (task_signature,),
        )
        return await cur.fetchone() is not None


# ---- 签名统计与看板（V1） ---------------------------------------------------


async def success_count_by_signature(task_signature: str, db_path: str | None = None) -> int:
    """同签名成功轨迹数（技能蒸馏触发条件用）。"""
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT COUNT(*) FROM trajectories WHERE task_signature = ? AND outcome = 'success'",
            (task_signature,),
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def successful_trajectories(
    task_signature: str, *, limit: int = 5, db_path: str | None = None
) -> list[dict[str, Any]]:
    """同签名最近 N 条成功轨迹（蒸馏输入素材）。"""
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT id, session_id, intent_json, active_skill_id, tool_fp, answer_digest, ts"
            " FROM trajectories WHERE task_signature = ? AND outcome = 'success'"
            " ORDER BY id DESC LIMIT ?",
            (task_signature, max(1, limit)),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "session_id": r[1],
            "intent_json": r[2],
            "active_skill_id": r[3] or "",
            "tool_fp": r[4] or "",
            "answer_digest": r[5] or "",
            "ts": r[6],
        }
        for r in rows
    ]


async def stats_summary(db_path: str | None = None) -> dict[str, Any]:
    """进化看板统计（信号分布 / 经验数 / 草稿数 / 均分）。"""
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)

        async def _one(sql: str) -> Any:
            cur = await conn.execute(sql)
            row = await cur.fetchone()
            return row[0] if row else None

        signals_total = await _one("SELECT COUNT(*) FROM evaluation_signals")
        user_signals = await _one("SELECT COUNT(*) FROM evaluation_signals WHERE source = 'user'")
        user_up = await _one(
            "SELECT COUNT(*) FROM evaluation_signals WHERE source = 'user' AND rating = 1"
        )
        judge_avg = await _one(
            "SELECT AVG(rating) FROM evaluation_signals WHERE source = 'judge' AND rating > 0"
        )
        env_fail = await _one(
            "SELECT COUNT(*) FROM evaluation_signals WHERE source = 'env' AND score = 0"
        )
        experiences_active = await _one("SELECT COUNT(*) FROM experiences WHERE status = 'active'")
        drafts_pending = await _one("SELECT COUNT(*) FROM skill_drafts WHERE status = 'draft'")
    return {
        "signals_total": int(signals_total or 0),
        "user_signals": int(user_signals or 0),
        "user_up": int(user_up or 0),
        "env_fail": int(env_fail or 0),
        "judge_avg": round(float(judge_avg), 2) if judge_avg is not None else None,
        "experiences_active": int(experiences_active or 0),
        "drafts_pending": int(drafts_pending or 0),
    }


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


async def has_experience_for_session(session_id: str, db_path: str | None = None) -> bool:
    """该会话（run）是否已产出过经验（反思去重用：防同轨迹被 env + 用户 👎 双重反思）。"""
    async with aiosqlite.connect(_db_target(db_path)) as conn:
        await _ensure_schema(conn)
        cur = await conn.execute(
            "SELECT 1 FROM experiences WHERE source_session = ? LIMIT 1",
            (session_id,),
        )
        return await cur.fetchone() is not None


# ---- 经验库（读侧，同步 —— extra_rules 注入通道）---------------------------

# 同步通道建表标志缓存：检索是每次任务组装提示词的热路径，不能每次都执行
# DDL（隐式写事务，与异步写侧并发时更易触发 database is locked）；按库的绝对路径
# 缓存「已建表」（相对路径会随 cwd 漂移，测试隔离靠 chdir），失败时剔除缓存项。
_SYNC_SCHEMA_READY: set[str] = set()


def _ensure_sync_schema(conn: sqlite3.Connection, target: str) -> None:
    key = os.path.abspath(target)
    if key in _SYNC_SCHEMA_READY:
        return
    conn.executescript(SCHEMA_CREATE_TABLES)
    _SYNC_SCHEMA_READY.add(key)


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
    target = _db_target(db_path)
    try:
        conn = sqlite3.connect(target, timeout=2)
        try:
            _ensure_sync_schema(conn, target)
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
        _SYNC_SCHEMA_READY.discard(os.path.abspath(target))  # 库可能被外部重建，下次重试建表
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
