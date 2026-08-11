"""loganalysis.storage —— Phase 2F+ V1 日志分析 SQLite DAO。

设计：
- 单文件 SQLite（~/.eaide/log_analysis.db）
- 物理隔离：与 audit.sqlite / knowledge.db / biznav.db / log_index.db 等全独立
- 3 张表：search_cache / tail_sessions / log_analysis_cache
- BLOB 编解码与 logviewer/storage.rs::encode_u64_le 兼容（同字节序 + 4 字节 float）

并发：
- 短连接 + WAL + foreign_keys=ON
- Tests 用 `tmp_path` fixture 隔离（_isolate autouse 已在 conftest）
"""

from __future__ import annotations

import logging
import sqlite3
import struct
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent.loganalysis.models import AnalysisCacheEntry

logger = logging.getLogger(__name__)


# ---- 内嵌 schema（从 schema.sql 同步加载）----------------------------------


def _load_schema_sql() -> str:
    """从同包 schema.sql 读 schema。"""
    import importlib.resources as resources

    try:
        return (
            resources.files("agent.loganalysis").joinpath("schema.sql").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, AttributeError, ModuleNotFoundError):
        here = Path(__file__).resolve().parent
        return (here / "schema.sql").read_text(encoding="utf-8")


SCHEMA_SQL = _load_schema_sql()


# ---- BLOB 编码 / 解码（u64 LE，与 logviewer/storage.rs 对齐）-----------


def encode_u64_le(values: list[int]) -> bytes:
    """list[int] → bytes（u64 little-endian；空 → 空 BLOB）。"""
    if not values:
        return b""
    # single struct.pack call —— 性能 OK
    return struct.pack(f"<{len(values)}Q", *values)


def decode_u64_le(blob: bytes) -> list[int]:
    """bytes → list[int]（u64 LE）。"""
    if not blob:
        return []
    n = len(blob) // 8
    if n == 0:
        return []
    out = struct.unpack(f"<{n}Q", blob[: n * 8])
    return list(out)


# ---- LogAnalysisStorage ---------------------------------------------------


class LogAnalysisStorage:
    """日志分析 SQLite DAO。

    用法：
        storage = LogAnalysisStorage("path/to/log_analysis.db")
        entry = storage.get_search_cache(file, pattern, "literal", fp)
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    # ---- search_cache --------------------------------------------------

    def get_search_cache(
        self,
        file_path: str,
        pattern: str,
        pattern_type: str,
        file_fingerprint: str,
    ) -> tuple[list[int], int] | None:
        """查询搜索缓存；返 (matched_lines, match_count) 或 None（未命中 / 过期）。"""
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT matched_lines, match_count, expires_at
                FROM search_cache
                WHERE file_path = ? AND pattern = ? AND pattern_type = ?
                  AND file_fingerprint = ? AND expires_at > ?
                ORDER BY searched_at DESC LIMIT 1
                """,
                (file_path, pattern, pattern_type, file_fingerprint, now),
            ).fetchone()
        if row is None:
            return None
        return (decode_u64_le(bytes(row["matched_lines"])), int(row["match_count"]))

    def upsert_search_cache(
        self,
        file_path: str,
        pattern: str,
        pattern_type: str,
        file_fingerprint: str,
        matched_lines: list[int],
        ttl_sec: int = 3600,
    ) -> None:
        """写 / 更新搜索缓存（按 file_path + pattern + pattern_type + fingerprint 四元组）。"""
        now = int(time.time())
        blob = encode_u64_le(matched_lines)
        with self._connect() as conn:
            # 先删旧（同四元组）
            conn.execute(
                """
                DELETE FROM search_cache
                WHERE file_path = ? AND pattern = ? AND pattern_type = ?
                  AND file_fingerprint = ?
                """,
                (file_path, pattern, pattern_type, file_fingerprint),
            )
            conn.execute(
                """
                INSERT INTO search_cache
                  (file_path, pattern, pattern_type, file_fingerprint,
                   matched_lines, match_count, searched_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_path,
                    pattern,
                    pattern_type,
                    file_fingerprint,
                    blob,
                    len(matched_lines),
                    now,
                    now + ttl_sec,
                ),
            )

    def cleanup_search_cache(self, now: int | None = None) -> int:
        """清理过期 search_cache 记录。Returns: 删除条数。"""
        if now is None:
            now = int(time.time())
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM search_cache WHERE expires_at <= ?", (now,))
        return cur.rowcount

    # ---- tail_sessions --------------------------------------------------

    def create_tail_session(self, session_id: str, file_path: str) -> int:
        """新建 tail 会话；返回 id。"""
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tail_sessions
                  (session_id, file_path, last_position, lines_emitted, started_at, updated_at)
                VALUES (?, ?, 0, 0, ?, ?)
                """,
                (session_id, file_path, now, now),
            )
        return int(cur.lastrowid or 0)

    def update_tail_session(
        self,
        session_id: str,
        *,
        last_position: int | None = None,
        lines_emitted_increment: int | None = None,
    ) -> bool:
        """更新 tail 会话位置 / emit 计数。Returns: True if row exists."""
        now = int(time.time())
        sets: list[str] = ["updated_at = ?"]
        params: list = [now]
        if last_position is not None:
            sets.append("last_position = ?")
            params.append(int(last_position))
        if lines_emitted_increment is not None and lines_emitted_increment != 0:
            sets.append("lines_emitted = lines_emitted + ?")
            params.append(int(lines_emitted_increment))
        params.append(session_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE tail_sessions SET {', '.join(sets)} WHERE session_id = ?",
                params,
            )
        return cur.rowcount > 0

    def end_tail_session(self, session_id: str) -> bool:
        """结束 tail 会话（写 ended_at = now）。"""
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE tail_sessions SET ended_at = ?, updated_at = ? "
                "WHERE session_id = ? AND ended_at IS NULL",
                (now, now, session_id),
            )
        return cur.rowcount > 0

    def get_tail_session(self, session_id: str) -> dict | None:
        """取 tail 会话状态。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tail_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_active_tail_sessions(self, file_path: str | None = None) -> list[dict]:
        """列活跃 tail 会话（ended_at IS NULL）。"""
        sql = "SELECT * FROM tail_sessions WHERE ended_at IS NULL"
        params: list = []
        if file_path:
            sql += " AND file_path = ?"
            params.append(file_path)
        sql += " ORDER BY started_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---- log_analysis_cache ---------------------------------------------

    def get_analysis_cache(self, cache_key: str) -> AnalysisCacheEntry | None:
        """按 cache_key 查询；过期返 None。"""
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM log_analysis_cache WHERE cache_key = ? AND expires_at > ?",
                (cache_key, now),
            ).fetchone()
        if row is None:
            return None
        return AnalysisCacheEntry(
            id=int(row["id"]),
            cache_key=str(row["cache_key"]),
            file_path=str(row["file_path"]),
            file_fingerprint=str(row["file_fingerprint"]),
            analysis_type=str(row["analysis_type"]),
            payload_json=str(row["payload_json"]),
            created_at=int(row["created_at"]),
            expires_at=int(row["expires_at"]),
        )

    def upsert_analysis_cache(self, entry: AnalysisCacheEntry) -> None:
        """写 / 更新 analysis 缓存。"""
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM log_analysis_cache WHERE cache_key = ?",
                (entry.cache_key,),
            )
            conn.execute(
                """
                INSERT INTO log_analysis_cache
                  (cache_key, file_path, file_fingerprint, analysis_type,
                   payload_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.cache_key,
                    entry.file_path,
                    entry.file_fingerprint,
                    entry.analysis_type,
                    entry.payload_json,
                    now,
                    entry.expires_at,
                ),
            )

    def cleanup_analysis_cache(self, now: int | None = None) -> int:
        """清理过期 log_analysis_cache。"""
        if now is None:
            now = int(time.time())
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM log_analysis_cache WHERE expires_at <= ?",
                (now,),
            )
        return cur.rowcount

    # ---- 统计 -----------------------------------------------------------

    def get_stats(self) -> dict:
        """3 张表的行数 + 活跃 tail 会话数。"""
        with self._connect() as conn:
            search_n = int(conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0])
            tail_n = int(conn.execute("SELECT COUNT(*) FROM tail_sessions").fetchone()[0])
            active_n = int(
                conn.execute(
                    "SELECT COUNT(*) FROM tail_sessions WHERE ended_at IS NULL"
                ).fetchone()[0]
            )
            cache_n = int(conn.execute("SELECT COUNT(*) FROM log_analysis_cache").fetchone()[0])
        return {
            "search_cache_rows": search_n,
            "tail_sessions_rows": tail_n,
            "tail_sessions_active": active_n,
            "log_analysis_cache_rows": cache_n,
        }


# ---- 单例工厂 -------------------------------------------------------------


_default_storage: LogAnalysisStorage | None = None


def get_default_storage() -> LogAnalysisStorage:
    global _default_storage
    if _default_storage is None:
        from agent.config import settings

        db_path = getattr(settings, "log_analysis_db_path", None) or (
            str(Path.home() / ".eaide" / "log_analysis.db")
        )
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _default_storage = LogAnalysisStorage(db_path)
    return _default_storage


def reset_default_storage() -> None:
    """测试 hook。"""
    global _default_storage
    _default_storage = None
