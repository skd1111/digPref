"""SQLite 审计存储。与 src-tauri/src/audit/store.rs 共享同一份 schema。

V1.5 (2026-07-31)：audit 表新增 5 列（correlation_id / actor_type / event_type /
task_id / parent_task_id），用于 Phase 12 子 Agent 决策树回放。INSERT 列序必须与
Rust 端 schema.sql 严格镜像（CLAUDE.md §6 红线）。

兼容性：保留旧 `audit(action, payload)` 签名，新增 5 个可选 kwargs；旧调用方零改动。
升级路径：对已存在的旧库执行 ALTER TABLE ADD COLUMN（捕获 "duplicate column" 错）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from agent.config import settings

logger = logging.getLogger(__name__)

_LOCK = asyncio.Lock()

# V1.5：子 Agent 决策树回放所需 5 列。ALTER TABLE 兼容老库；
# 新建库由 SCHEMA 一次性 CREATE。
_V15_COLUMNS = (
    "correlation_id TEXT",
    "actor_type TEXT",
    "event_type TEXT",
    "task_id TEXT",
    "parent_task_id TEXT",
)


async def audit(
    action: str,
    payload: dict,
    *,
    run_id: str | None = None,
    correlation_id: str | None = None,
    actor_type: Optional[str] = None,
    event_type: Optional[str] = None,
    task_id: Optional[str] = None,
    parent_task_id: Optional[str] = None,
    db_path: str | None = None,
) -> None:
    """Append an entry to the audit log. Fire-and-forget, but durable.

    V1.5 新增 5 个 kwargs：
      - correlation_id: 同一棵决策树的所有事件共享此 ID
      - actor_type: 'user' / 'main_agent' / 'sub_agent' / 'system'
      - event_type: 结构化事件名（与 action 平行，向后兼容）
      - task_id: 子 Agent sub_agent_id
      - parent_task_id: 父 sub_agent_id

    `db_path` 优先（默认 None 时用 settings.audit_db_path）。
    显式传 db_path 可避免：pytest async 测试间 monkeypatch.chdir 不稳定时，
    写入路径与测试期望路径错位。
    """
    target = db_path or settings.audit_db_path
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    async with _LOCK, aiosqlite.connect(target) as db:
        # 1) 先建表（IF NOT EXISTS → 旧库跳过）
        await db.executescript(SCHEMA_CREATE_TABLE)
        # 2) 旧库 ALTER TABLE ADD COLUMN（新库已包含则跳过）
        await _ensure_v15_columns(db)
        # 3) 最后建索引（此时 correlation_id 列一定存在）
        await db.executescript(SCHEMA_INDEXES)
        await db.execute(
            "INSERT INTO audit("
            "  action, payload, ts, run_id,"
            "  correlation_id, actor_type, event_type, task_id, parent_task_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action,
                json.dumps(payload, ensure_ascii=False, default=str),
                datetime.now(timezone.utc).isoformat(),
                run_id,
                correlation_id,
                actor_type,
                event_type,
                task_id,
                parent_task_id,
            ),
        )
        await db.commit()


async def _ensure_v15_columns(db: aiosqlite.Connection) -> None:
    """对已存在的旧库加 5 列（不存在则跳过）。幂等。"""
    for col_def in _V15_COLUMNS:
        col_name = col_def.split()[0]
        try:
            await db.execute(f"ALTER TABLE audit ADD COLUMN {col_def}")
        except Exception as exc:  # noqa: BLE001
            # "duplicate column name" → 已存在，忽略
            msg = str(exc).lower()
            if "duplicate column" not in msg:
                logger.warning("ALTER TABLE audit ADD COLUMN %s 失败: %s", col_name, exc)


async def search(query: str, limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(settings.audit_db_path) as db:
        cur = await db.execute(
            "SELECT action, payload, ts, run_id FROM audit "
            "WHERE action LIKE ? OR payload LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        )
        rows = await cur.fetchall()
    return [
        {"action": a, "payload": json.loads(p), "ts": t, "run_id": r}
        for (a, p, t, r) in rows
    ]


async def search_by_correlation(
    correlation_id: str,
    limit: int = 500,
    db_path: str | None = None,
) -> list[dict]:
    """V1.5 新增：按 correlation_id 回放一整棵决策树。

    返回按时间升序的事件列表（先发生先返回），便于上溯决策树。
    """
    target = db_path or settings.audit_db_path
    async with aiosqlite.connect(target) as db:
        cur = await db.execute(
            "SELECT action, payload, ts, run_id, correlation_id, actor_type, "
            "       event_type, task_id, parent_task_id "
            "FROM audit WHERE correlation_id = ? "
            "ORDER BY id ASC LIMIT ?",
            (correlation_id, limit),
        )
        rows = await cur.fetchall()
    return [
        {
            "action": a,
            "payload": json.loads(p),
            "ts": t,
            "run_id": r,
            "correlation_id": c,
            "actor_type": ac,
            "event_type": ev,
            "task_id": tk,
            "parent_task_id": pt,
        }
        for (a, p, t, r, c, ac, ev, tk, pt) in rows
    ]


SCHEMA_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    payload         TEXT NOT NULL,
    ts              TEXT NOT NULL,
    operator        TEXT,
    run_id          TEXT,
    correlation_id  TEXT,
    actor_type      TEXT,
    event_type      TEXT,
    task_id         TEXT,
    parent_task_id  TEXT
);
"""

# 索引必须在 _ensure_v15_columns 之后执行，否则旧库缺少 correlation_id 列会报错
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_audit_action      ON audit(action);
CREATE INDEX IF NOT EXISTS idx_audit_ts          ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_run         ON audit(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit(correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor_event ON audit(actor_type, event_type);
CREATE INDEX IF NOT EXISTS idx_audit_task        ON audit(task_id);
"""