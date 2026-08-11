"""orchestrator.state_repo —— Phase 12 V1.5 权威持久层 + 乐观锁 CAS。

设计文档 §2.2「Redis 热路径 + SQLite 权威」：
    本模块是**权威层**。任何子 Agent 状态迁移（pending → running → ok/err/dlq）
    都在这里落盘；进程内 asyncio 结构只做加速。

铁律落地：
    - 铁律 6 幂等：`find_by_idempotency()` 保证同一 `idempotency_token` 不重复派发
    - 铁律 8 可回放：`correlation_id` 列 + audit_bridge 双写
    - 铁律 10 状态中心化：DLQ / 制品 / 指标都有表，不留进程内黑盒
    - 并发：`update_status_cas()` 带 `state_version` 条件 UPDATE，冲突抛
      `StateVersionConflict`，调用方重读最新版本再写

架构决策（2026-07-31）：不引入 Redis / PostgreSQL —— SQLite WAL 单文件即可。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent.config import settings

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class StateVersionConflict(RuntimeError):
    """乐观锁冲突：expected_version 与库中当前版本不一致。"""

    def __init__(self, task_id: str, expected: int, actual: int | None) -> None:
        super().__init__(f"state_version 冲突 task={task_id} expected={expected} actual={actual}")
        self.task_id = task_id
        self.expected = expected
        self.actual = actual


class StateRepo:
    """orchestrator.db 的异步 DAO。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.orchestrator_db_path
        self._lock = asyncio.Lock()

    @property
    def db_path(self) -> str:
        return self._db_path

    async def _connect(self) -> aiosqlite.Connection:
        parent = Path(self._db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        await db.commit()
        return db

    # ---- 任务 -----------------------------------------------------------

    async def save_task(
        self,
        *,
        task_id: str,
        parent_run_id: str,
        parent_task_id: str | None,
        correlation_id: str,
        idempotency_token: str,
        depth: int,
        task_type: str,
        priority: str,
        spec: dict[str, Any],
        status: str = "pending",
        local_only: bool = False,
        strategy: str | None = None,
    ) -> dict[str, Any]:
        """插入任务（幂等：已存在同 idempotency_token → 直接返回旧行）。"""
        existing = await self.find_by_idempotency(idempotency_token)
        if existing is not None:
            return existing
        async with self._lock:
            db = await self._connect()
            try:
                now = _now_iso()
                await db.execute(
                    """
                    INSERT OR REPLACE INTO sub_agent_tasks (
                        task_id, parent_run_id, parent_task_id, correlation_id,
                        idempotency_token, depth, task_type, priority, status,
                        attempts, state_version, strategy, local_only, backend,
                        tokens_before, tokens_after, latency_ms,
                        spec_json, report_json, error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, NULL,
                              0, 0, 0, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        task_id,
                        parent_run_id,
                        parent_task_id,
                        correlation_id,
                        idempotency_token,
                        depth,
                        task_type,
                        priority,
                        status,
                        strategy,
                        1 if local_only else 0,
                        _dumps(spec),
                        now,
                        now,
                    ),
                )
                await db.commit()
            finally:
                await db.close()
        row = await self.get_task(task_id)
        assert row is not None
        return row

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            cur = await db.execute("SELECT * FROM sub_agent_tasks WHERE task_id = ?", (task_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await db.close()

    async def find_by_idempotency(self, token: str) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            cur = await db.execute(
                "SELECT * FROM sub_agent_tasks WHERE idempotency_token = ?", (token,)
            )
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await db.close()

    async def update_status_cas(
        self,
        *,
        task_id: str,
        expected_version: int,
        status: str,
        attempts: int | None = None,
        report: dict[str, Any] | None = None,
        error: str | None = None,
        backend: str | None = None,
        strategy: str | None = None,
        tokens_before: int | None = None,
        tokens_after: int | None = None,
        latency_ms: int | None = None,
    ) -> int:
        """CAS 更新状态。成功返回新 state_version；冲突抛 StateVersionConflict。"""
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT state_version FROM sub_agent_tasks WHERE task_id = ?",
                    (task_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    raise StateVersionConflict(task_id, expected_version, None)
                actual = int(row["state_version"])
                if actual != expected_version:
                    raise StateVersionConflict(task_id, expected_version, actual)

                new_version = actual + 1
                sets = [
                    "status = ?",
                    "state_version = ?",
                    "updated_at = ?",
                ]
                params: list[Any] = [status, new_version, _now_iso()]
                for column, value in (
                    ("attempts", attempts),
                    ("report_json", _dumps(report) if report is not None else None),
                    ("error", error),
                    ("backend", backend),
                    ("strategy", strategy),
                    ("tokens_before", tokens_before),
                    ("tokens_after", tokens_after),
                    ("latency_ms", latency_ms),
                ):
                    if value is not None:
                        sets.append(f"{column} = ?")
                        params.append(value)
                params.extend([task_id, actual])
                await db.execute(
                    f"UPDATE sub_agent_tasks SET {', '.join(sets)} "
                    "WHERE task_id = ? AND state_version = ?",
                    params,
                )
                await db.commit()
                return new_version
            finally:
                await db.close()

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        correlation_id: str | None = None,
        parent_run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if correlation_id:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        if parent_run_id:
            clauses.append("parent_run_id = ?")
            params.append(parent_run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        db = await self._connect()
        try:
            cur = await db.execute(
                f"SELECT * FROM sub_agent_tasks {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            )
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()

    # ---- 制品 -----------------------------------------------------------

    async def save_artifact(
        self,
        *,
        artifact_id: str,
        task_id: str,
        kind: str = "summary",
        content_hash: str = "",
        byte_size: int = 0,
        preview: str = "",
        uri: str | None = None,
    ) -> None:
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO sub_agent_artifacts (
                        artifact_id, task_id, kind, content_hash,
                        byte_size, preview, uri, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (artifact_id, task_id, kind, content_hash, byte_size, preview, uri, _now_iso()),
                )
                await db.commit()
            finally:
                await db.close()

    async def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        db = await self._connect()
        try:
            cur = await db.execute(
                "SELECT * FROM sub_agent_artifacts WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            )
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()

    # ---- DLQ ------------------------------------------------------------

    async def push_dlq(
        self,
        *,
        task_id: str,
        correlation_id: str = "",
        idempotency_token: str = "",
        payload: dict[str, Any] | None = None,
        last_error: str = "",
        attempts: int = 0,
    ) -> None:
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO sub_agent_dlq (
                        task_id, correlation_id, idempotency_token, payload_json,
                        last_error, attempts, state, note, handled_by,
                        enqueued_at, handled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'open', NULL, NULL, ?, NULL)
                    """,
                    (
                        task_id,
                        correlation_id,
                        idempotency_token,
                        _dumps(payload or {}),
                        last_error,
                        attempts,
                        _now_iso(),
                    ),
                )
                await db.commit()
            finally:
                await db.close()

    async def list_dlq(
        self, *, state: str | None = "open", limit: int = 100
    ) -> list[dict[str, Any]]:
        db = await self._connect()
        try:
            if state:
                cur = await db.execute(
                    "SELECT * FROM sub_agent_dlq WHERE state = ? ORDER BY enqueued_at DESC LIMIT ?",
                    (state, limit),
                )
            else:
                cur = await db.execute(
                    "SELECT * FROM sub_agent_dlq ORDER BY enqueued_at DESC LIMIT ?",
                    (limit,),
                )
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await db.close()

    async def mark_dlq(
        self,
        *,
        task_id: str,
        state: str,
        note: str | None = None,
        handled_by: str | None = None,
    ) -> bool:
        """标记 DLQ 条目为 requeued / closed。返回 False = 条目不存在。"""
        if state not in ("open", "requeued", "closed"):
            raise ValueError(f"非法 DLQ state: {state!r}")
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "UPDATE sub_agent_dlq SET state = ?, note = ?, handled_by = ?, "
                    "handled_at = ? WHERE task_id = ?",
                    (state, note, handled_by, _now_iso(), task_id),
                )
                await db.commit()
                return cur.rowcount > 0
            finally:
                await db.close()

    # ---- 指标 -----------------------------------------------------------

    async def record_metric(
        self,
        *,
        metric: str,
        value: float,
        task_id: str | None = None,
        correlation_id: str | None = None,
        labels: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    """
                    INSERT INTO sub_agent_metrics (
                        task_id, correlation_id, metric, value, labels_json, ts
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        correlation_id,
                        metric,
                        float(value),
                        _dumps(labels or {}),
                        _now_iso(),
                    ),
                )
                await db.commit()
            finally:
                await db.close()

    async def metric_summary(self, metric: str) -> dict[str, Any]:
        db = await self._connect()
        try:
            cur = await db.execute(
                "SELECT COUNT(*) AS n, AVG(value) AS avg, MIN(value) AS min, "
                "MAX(value) AS max FROM sub_agent_metrics WHERE metric = ?",
                (metric,),
            )
            row = await cur.fetchone()
            return {
                "metric": metric,
                "count": int(row["n"] or 0),
                "avg": float(row["avg"] or 0.0),
                "min": float(row["min"] or 0.0),
                "max": float(row["max"] or 0.0),
            }
        finally:
            await db.close()

    # ---- 汇总 -----------------------------------------------------------

    async def stats(self) -> dict[str, Any]:
        db = await self._connect()
        try:
            cur = await db.execute(
                "SELECT status, COUNT(*) AS n FROM sub_agent_tasks GROUP BY status"
            )
            by_status = {r["status"]: int(r["n"]) for r in await cur.fetchall()}
            cur = await db.execute("SELECT COUNT(*) AS n FROM sub_agent_tasks")
            total = int((await cur.fetchone())["n"])
            cur = await db.execute("SELECT state, COUNT(*) AS n FROM sub_agent_dlq GROUP BY state")
            dlq = {r["state"]: int(r["n"]) for r in await cur.fetchall()}
            cur = await db.execute("SELECT COUNT(*) AS n FROM sub_agent_artifacts")
            artifacts = int((await cur.fetchone())["n"])
            return {
                "total_tasks": total,
                "by_status": by_status,
                "dlq": dlq,
                "artifacts": artifacts,
            }
        finally:
            await db.close()


# ---- 全局单例 -------------------------------------------------------------

_default_repo: StateRepo | None = None


def get_default_repo() -> StateRepo:
    global _default_repo
    if _default_repo is None:
        _default_repo = StateRepo()
    return _default_repo


def reset_default_repo(db_path: str | None = None) -> StateRepo:
    """测试 hook：重建 StateRepo（默认读 settings，pytest 已 chdir 到 tmp_path）。"""
    global _default_repo
    _default_repo = StateRepo(db_path=db_path)
    return _default_repo


__all__ = [
    "StateRepo",
    "StateVersionConflict",
    "get_default_repo",
    "reset_default_repo",
]
