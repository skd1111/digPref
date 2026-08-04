"""Phase 5 V0 · 审核专家 SQLite 存储 —— 4 表 CRUD。

V0 物理隔离：
    audit_expert.db 与 audit / tool_calls / ssh / image_processing 等 12 个 db 独立。
    （不与 audit 共享 —— 审批工作台是金融审计专属高频模块，独立 DB 减少互锁）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent.config import settings


_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditExpertStorage:
    """audit_expert.db 包装。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.audit_expert_db_path
        self._lock = asyncio.Lock()

    async def _connect(self) -> aiosqlite.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        await db.commit()
        return db

    # ---- approval_tasks ------------------------------------------------

    async def insert_task(
        self,
        *,
        task_id: str,
        run_id: str,
        title: str,
        description: str,
        risk_level: str,
        pending_tool_call: dict,
        requested_by: str,
        meta: dict[str, Any],
        dual_required: bool = False,
    ) -> int:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    """
                    INSERT INTO approval_tasks (
                        task_id, run_id, title, description, risk_level,
                        status, pending_tool_call_json,
                        requested_by, requested_at, dual_required, meta_json, ts
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id, run_id, title, description, risk_level,
                        json.dumps(pending_tool_call, ensure_ascii=False, default=str),
                        requested_by, _now_iso(),
                        1 if dual_required else 0,
                        json.dumps(meta, ensure_ascii=False, default=str),
                        _now_iso(),
                    ),
                )
                await db.commit()
                return cur.lastrowid or 0
            finally:
                await db.close()

    async def get_task(self, task_id: str) -> dict | None:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM approval_tasks WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                    (task_id,),
                )
                row = await cur.fetchone()
            finally:
                await db.close()
        return _task_row_to_dict(row) if row else None

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        risk_level: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if risk_level:
            where.append("risk_level = ?")
            params.append(risk_level)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        params.append(limit)
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    f"SELECT * FROM approval_tasks {where_sql} ORDER BY id DESC LIMIT ?",
                    tuple(params),
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_task_row_to_dict(r) for r in rows]

    async def update_task_decision(
        self,
        task_id: str,
        *,
        status: str,
        decided_by: str,
        decision_reason: str,
        mfa_verified: bool,
        second_approver: str | None = None,
    ) -> bool:
        """更新决策（V1 扩展：双人复核时 second_approver 单独记录）。"""
        async with self._lock:
            db = await self._connect()
            try:
                # 双人复核时，状态变化：
                #   1) 第一次 approve → status 仍 pending，second_approver 待定
                #   2) 第二次 approve → status 改 approved
                # V1 简化：用 update_first_approver / update_second_approver 单独记录
                cur = await db.execute(
                    """
                    UPDATE approval_tasks
                    SET status = ?, decided_by = ?, decided_at = ?,
                        decision_reason = ?, mfa_verified = ?
                    WHERE task_id = ?
                    """,
                    (status, decided_by, _now_iso(), decision_reason,
                     1 if mfa_verified else 0, task_id),
                )
                await db.commit()
                return cur.rowcount > 0
            finally:
                await db.close()

    async def record_first_approver(
        self, task_id: str, actor: str, reason: str,
    ) -> bool:
        """记录第一审批人（双人复核模式）。

        V1.5 fix：原条件 `first_approver = ''` 对 NULL 不匹配；改为 IS NULL OR = ''
        """
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    """
                    UPDATE approval_tasks
                    SET first_approver = ?, first_approver_signed_at = ?,
                        decision_reason = ?
                    WHERE task_id = ?
                      AND (first_approver IS NULL OR first_approver = '')
                    """,
                    (actor, _now_iso(), reason, task_id),
                )
                await db.commit()
                return cur.rowcount > 0
            finally:
                await db.close()

    async def record_second_approver(
        self, task_id: str, actor: str, reason: str,
    ) -> bool:
        """记录第二审批人 + 决策完成。

        V1.5 fix：原 SQL 缺 decided_at 占位符（6 ? 但只传 5）。
        """
        async with self._lock:
            db = await self._connect()
            try:
                now = _now_iso()
                cur = await db.execute(
                    """
                    UPDATE approval_tasks
                    SET second_approver = ?, second_approver_signed_at = ?,
                        status = 'approved', decided_by = ?, decided_at = ?,
                        decision_reason = ?
                    WHERE task_id = ?
                    """,
                    (actor, now, actor, now, reason, task_id),
                )
                await db.commit()
                return cur.rowcount > 0
            finally:
                await db.close()

    # ---- approval_actions (签名链) -----------------------------------------

    async def insert_action(
        self,
        *,
        action_id: str,
        task_id: str,
        action_type: str,
        actor: str,
        reason: str,
        mfa_verified: bool,
        timestamp: str,
        prev_hash: str,
        signature_hash: str,
        totp_code_hash: str | None = None,
        rsa_signature: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    """
                    INSERT INTO approval_actions (
                        action_id, task_id, action_type, actor, reason,
                        mfa_verified, totp_code_hash, timestamp,
                        prev_hash, signature_hash, rsa_signature, meta_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_id, task_id, action_type, actor, reason,
                        1 if mfa_verified else 0, totp_code_hash, timestamp,
                        prev_hash, signature_hash, rsa_signature,
                        json.dumps(meta or {}, ensure_ascii=False, default=str),
                    ),
                )
                await db.commit()
                return cur.lastrowid or 0
            finally:
                await db.close()

    async def list_actions(self, task_id: str) -> list[dict]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM approval_actions WHERE task_id = ? ORDER BY timestamp ASC",
                    (task_id,),
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_action_row_to_dict(r) for r in rows]

    async def get_last_action_hash(self, task_id: str) -> str:
        """获取 task 的最后一条 action 的 signature_hash（用于链式签名）。"""
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT signature_hash FROM approval_actions WHERE task_id = ? "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (task_id,),
                )
                row = await cur.fetchone()
            finally:
                await db.close()
        return row[0] if row else ""

    # ---- evidence_entries -----------------------------------------------

    async def insert_evidence(
        self,
        *,
        evidence_id: str,
        task_id: str,
        evidence_type: str,
        title: str,
        content: dict,
        source: str,
    ) -> int:
        content_json = json.dumps(content, ensure_ascii=False, default=str)
        content_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    """
                    INSERT INTO evidence_entries (
                        evidence_id, task_id, evidence_type, title,
                        content_json, source, timestamp, hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id, task_id, evidence_type, title,
                        content_json, source, _now_iso(), content_hash,
                    ),
                )
                await db.commit()
                return cur.lastrowid or 0
            finally:
                await db.close()

    async def list_evidence(self, task_id: str) -> list[dict]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM evidence_entries WHERE task_id = ? ORDER BY timestamp ASC",
                    (task_id,),
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_evidence_row_to_dict(r) for r in rows]

    # ---- compliance_checks ---------------------------------------------

    async def insert_compliance(
        self,
        *,
        check_id: str,
        task_id: str,
        rule_name: str,
        level: str,
        message: str,
        passed: bool,
    ) -> int:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    """
                    INSERT INTO compliance_checks (
                        check_id, task_id, rule_name, level, message,
                        passed, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        check_id, task_id, rule_name, level, message,
                        1 if passed else 0, _now_iso(),
                    ),
                )
                await db.commit()
                return cur.lastrowid or 0
            finally:
                await db.close()

    async def list_compliance(self, task_id: str) -> list[dict]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM compliance_checks WHERE task_id = ? ORDER BY timestamp ASC",
                    (task_id,),
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_compliance_row_to_dict(r) for r in rows]

    # ---- stats ----------------------------------------------------------

    async def get_stats(self) -> dict:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT status, COUNT(*) as n FROM approval_tasks GROUP BY status"
                )
                task_rows = await cur.fetchall()
                cur2 = await db.execute(
                    "SELECT level, COUNT(*) as n FROM compliance_checks "
                    "WHERE passed = 0 GROUP BY level"
                )
                fail_rows = await cur2.fetchall()
            finally:
                await db.close()
        return {
            "tasks_by_status": {s: n for s, n in task_rows},
            "compliance_failures_by_level": {l: n for l, n in fail_rows},
        }


# ---- 行 → dict helpers ----------------------------------------------------

def _task_row_to_dict(row: tuple) -> dict:
    cols = [
        "id", "task_id", "run_id", "title", "description", "risk_level",
        "status", "pending_tool_call_json", "requested_by", "requested_at",
        "decided_by", "decided_at", "decision_reason", "mfa_verified",
        "dual_required", "first_approver", "second_approver",
        "first_approver_signed_at", "second_approver_signed_at",
        "meta_json", "ts",
    ]
    d = dict(zip(cols, row))
    if d.get("pending_tool_call_json"):
        try:
            d["pending_tool_call"] = json.loads(d["pending_tool_call_json"])
        except (json.JSONDecodeError, TypeError):
            d["pending_tool_call"] = {}
    if d.get("meta_json"):
        try:
            d["meta"] = json.loads(d["meta_json"])
        except (json.JSONDecodeError, TypeError):
            d["meta"] = {}
    # 布尔转换
    d["dual_required"] = bool(d.get("dual_required", 0))
    d["mfa_verified"] = bool(d.get("mfa_verified", 0))
    return d


def _action_row_to_dict(row: tuple) -> dict:
    cols = [
        "id", "action_id", "task_id", "action_type", "actor", "reason",
        "mfa_verified", "totp_code_hash", "timestamp", "prev_hash",
        "signature_hash", "rsa_signature", "meta_json",
    ]
    d = dict(zip(cols, row))
    if d.get("meta_json"):
        try:
            d["meta"] = json.loads(d["meta_json"])
        except (json.JSONDecodeError, TypeError):
            d["meta"] = {}
    d["mfa_verified"] = bool(d.get("mfa_verified", 0))
    return d


def _evidence_row_to_dict(row: tuple) -> dict:
    cols = [
        "id", "evidence_id", "task_id", "evidence_type", "title",
        "content_json", "source", "timestamp", "hash",
    ]
    d = dict(zip(cols, row))
    if d.get("content_json"):
        try:
            d["content"] = json.loads(d["content_json"])
        except (json.JSONDecodeError, TypeError):
            d["content"] = {}
    return d


def _compliance_row_to_dict(row: tuple) -> dict:
    cols = [
        "id", "check_id", "task_id", "rule_name", "level", "message",
        "passed", "timestamp",
    ]
    return dict(zip(cols, row))


# ---- 单例工厂 -------------------------------------------------------------

_DEFAULT_STORAGE: AuditExpertStorage | None = None


def get_default_storage() -> AuditExpertStorage:
    global _DEFAULT_STORAGE
    if _DEFAULT_STORAGE is None:
        _DEFAULT_STORAGE = AuditExpertStorage()
    return _DEFAULT_STORAGE


def reset_default_storage() -> None:
    global _DEFAULT_STORAGE
    _DEFAULT_STORAGE = None