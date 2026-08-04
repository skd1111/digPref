"""Phase 2B V0 · ssh_sessions + ssh_commands 表 CRUD。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent.config import settings


_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SshStorage:
    """ssh.db SQLite 包装。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.ssh_db_path
        self._lock = asyncio.Lock()

    async def _connect(self) -> aiosqlite.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        await db.commit()
        return db

    async def insert_session(
        self,
        *,
        session_id: str,
        host: str,
        port: int,
        username: str,
        auth_method: str,
        status: str,
        pty_mode: str,
        meta: dict[str, Any],
        error: str | None = None,
    ) -> int:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    """
                    INSERT INTO ssh_sessions (
                        session_id, host, port, username, auth_method,
                        status, pty_mode, created_at, last_used, meta_json, error, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id, host, port, username, auth_method,
                        status, pty_mode, _now_iso(), _now_iso(),
                        json.dumps(meta, ensure_ascii=False, default=str),
                        error, _now_iso(),
                    ),
                )
                await db.commit()
                return cur.lastrowid or 0
            finally:
                await db.close()

    async def update_session_status(
        self,
        session_id: str,
        status: str,
        error: str | None = None,
    ) -> bool:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "UPDATE ssh_sessions SET status = ?, error = ?, disconnected_at = ? WHERE session_id = ?",
                    (status, error, _now_iso(), session_id),
                )
                await db.commit()
                return cur.rowcount > 0
            finally:
                await db.close()

    async def touch_session(self, session_id: str) -> bool:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "UPDATE ssh_sessions SET last_used = ? WHERE session_id = ?",
                    (_now_iso(), session_id),
                )
                await db.commit()
                return cur.rowcount > 0
            finally:
                await db.close()

    async def list_sessions(self, limit: int = 50) -> list[dict]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM ssh_sessions ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_row_to_dict(r) for r in rows]

    async def insert_command(
        self,
        *,
        session_id: str,
        command: str,
        exit_code: int | None,
        elapsed_ms: int,
        ok: bool,
        error: str | None,
        stdout: str,
        stderr: str,
        stdout_head_bytes: int = 4096,
        stderr_head_bytes: int = 4096,
    ) -> int:
        # 截断保存（前 4KB stdout / stderr）
        stdout_head = stdout[:stdout_head_bytes] if stdout else None
        stderr_head = stderr[:stderr_head_bytes] if stderr else None
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    """
                    INSERT INTO ssh_commands (
                        session_id, command, exit_code, elapsed_ms, ok, error,
                        stdout_head, stderr_head, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id, command, exit_code, elapsed_ms,
                        1 if ok else 0, error,
                        stdout_head, stderr_head, _now_iso(),
                    ),
                )
                await db.commit()
                return cur.lastrowid or 0
            finally:
                await db.close()

    async def list_commands(self, session_id: str, limit: int = 50) -> list[dict]:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT * FROM ssh_commands WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                )
                rows = await cur.fetchall()
            finally:
                await db.close()
        return [_cmd_row_to_dict(r) for r in rows]

    async def get_stats(self) -> dict:
        async with self._lock:
            db = await self._connect()
            try:
                cur = await db.execute(
                    "SELECT host, COUNT(*) as n FROM ssh_sessions GROUP BY host"
                )
                rows = await cur.fetchall()
                cur2 = await db.execute(
                    "SELECT ok, COUNT(*) as n FROM ssh_commands GROUP BY ok"
                )
                cmd_rows = await cur2.fetchall()
            finally:
                await db.close()
        return {
            "sessions_by_host": {h: n for h, n in rows},
            "commands_by_status": {("ok" if ok else "fail"): n for ok, n in cmd_rows},
        }


def _row_to_dict(row: tuple) -> dict:
    cols = [
        "id", "session_id", "host", "port", "username", "auth_method",
        "status", "pty_mode", "created_at", "last_used", "disconnected_at",
        "meta_json", "error", "ts",
    ]
    d = dict(zip(cols, row))
    if d.get("meta_json"):
        try:
            d["meta"] = json.loads(d["meta_json"])
        except (json.JSONDecodeError, TypeError):
            d["meta"] = {}
    return d


def _cmd_row_to_dict(row: tuple) -> dict:
    cols = [
        "id", "session_id", "command", "exit_code", "elapsed_ms",
        "ok", "error", "stdout_head", "stderr_head", "ts",
    ]
    return dict(zip(cols, row))


_DEFAULT_STORAGE: SshStorage | None = None


def get_default_storage() -> SshStorage:
    global _DEFAULT_STORAGE
    if _DEFAULT_STORAGE is None:
        _DEFAULT_STORAGE = SshStorage()
    return _DEFAULT_STORAGE


def reset_default_storage() -> None:
    global _DEFAULT_STORAGE
    _DEFAULT_STORAGE = None