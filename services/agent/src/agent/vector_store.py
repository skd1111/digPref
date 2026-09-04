"""vector_store —— sqlite-vec 统一访问层（全仓向量存储唯一入口，2026-09-01）。

所有持久化/缓存向量统一落 SQLite 的 sqlite-vec 扩展（vec0 虚拟表），
与本地向量模型（llm/embedding.py 统一入口）形成端侧闭环：

    - vec0 虚拟表存向量（float32 little-endian BLOB，与 knowledge 旧
      encode_embedding 同格式）；
    - 检索用 vec_distance_cosine 标量函数（各场景数据量小，全量扫描即可
      保持与内存余弦**完全一致**的语义；规模上来后改 vec0 KNN 一行 SQL）；
    - 扩展加载失败（离线分发包缺 .dll 等）→ vec_ready 返 False，调用方
      按既有 best-effort 红线静默回退（返空 / 现算），绝不阻塞主链路。

用法：
    - 同步 sqlite3：直接调 `load_extension` / `ensure_vec_table` / `upsert`…
    - aiosqlite：所有同步函数经 `run_async(aio_conn, fn)` 丢到连接的
      worker 线程执行（sqlite 对象不得跨线程，扩展加载同理）。

红线：
    - 向量全部本地，不出内网（与 _LOCAL_ONLY_TASKS 同源约束）；
    - 零向量不入库（= embedding 服务异常信号，入库只会污染检索）。
"""

from __future__ import annotations

import logging
import sqlite3
import struct
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("agent.vector_store")

_T = TypeVar("_T")


# ---- 序列化（与 knowledge.storage 旧 encode_embedding 同格式）----------------


def serialize(vec: list[float]) -> bytes:
    """float 列表 → float32 little-endian BLOB。"""
    return struct.pack(f"<{len(vec)}f", *vec)


def deserialize(blob: bytes) -> list[float]:
    """float32 little-endian BLOB → float 列表。"""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob[: n * 4]))


# ---- 扩展加载 ----------------------------------------------------------------


def load_extension(conn: sqlite3.Connection) -> bool:
    """在同步 sqlite3 连接上加载 sqlite-vec；失败返 False（调用方静默回退）。"""
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        return True
    except Exception as exc:
        logger.debug("sqlite-vec load failed: %s", exc)
        return False


def vec_ready(conn: sqlite3.Connection) -> bool:
    """连接上 vec 函数族是否可用。"""
    try:
        conn.execute("SELECT vec_version()").fetchone()
        return True
    except Exception:
        return False


async def load_extension_async(aio_conn: Any) -> bool:
    """aiosqlite 连接：扩展必须在连接自己的 worker 线程里加载。"""
    try:
        import sqlite_vec

        def _load(raw: sqlite3.Connection) -> None:
            raw.enable_load_extension(True)
            raw.load_extension(sqlite_vec.loadable_path())

        await run_async(aio_conn, _load)
        return True
    except Exception as exc:
        logger.debug("sqlite-vec load (aiosqlite) failed: %s", exc)
        return False


async def vec_ready_async(aio_conn: Any) -> bool:
    """aiosqlite 版 vec_ready。"""
    try:
        return bool(
            await run_async(aio_conn, lambda raw: raw.execute("SELECT vec_version()").fetchone())
        )
    except Exception:
        return False


async def run_async(aio_conn: Any, fn: Callable[[sqlite3.Connection], _T]) -> _T:
    """把同步 fn(raw_conn) 调度到 aiosqlite 连接的 worker 线程执行。

    aiosqlite 无公开的底层连接访问器，`_conn` / `_execute` 是其稳定事实
    接口（扩展加载、vec0 DDL 等只能在持有连接的线程里做）。
    """
    box: dict[str, _T] = {}

    def _run() -> None:
        box["v"] = fn(aio_conn._conn)  # aiosqlite 底层连接（私有但稳定事实接口）

    await aio_conn._execute(_run)
    return box["v"]


# ---- 表管理 ------------------------------------------------------------------


def table_dim(conn: sqlite3.Connection, name: str, col: str = "embedding") -> int | None:
    """已存在 vec0 表的维度；不存在返 None。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if row is None:
        return None
    marker = f"{col} float["
    sql = str(row[0])
    start = sql.find(marker)
    if start < 0:
        return None
    try:
        return int(sql[start + len(marker) : sql.index("]", start)])
    except ValueError:
        return None


def ensure_vec_table(conn: sqlite3.Connection, name: str, dim: int, col: str = "embedding") -> bool:
    """确保 vec0 虚拟表存在且维度匹配；维度漂移（换模型）时重建。"""
    if dim <= 0:
        return False
    try:
        current = table_dim(conn, name, col)
        if current == dim:
            return True
        if current is not None:
            conn.execute(f"DROP TABLE {name}")
        conn.execute(f"CREATE VIRTUAL TABLE {name} USING vec0({col} float[{dim}])")
        return True
    except Exception as exc:
        logger.debug("ensure_vec_table(%s, dim=%s) failed: %s", name, dim, exc)
        return False


# ---- 读写 --------------------------------------------------------------------


def upsert(
    conn: sqlite3.Connection, table: str, rowid: int, vec: list[float], col: str = "embedding"
) -> bool:
    """写入/覆盖一条向量（零向量拒绝入库）。

    陷阱：vec0 虚拟表不支持 INSERT OR REPLACE（UNIQUE constraint），
    覆盖必须先 DELETE 再 INSERT。
    """
    if not vec or not any(vec):
        return False
    try:
        conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
        conn.execute(
            f"INSERT INTO {table}(rowid, {col}) VALUES (?, ?)",
            (rowid, serialize(vec)),
        )
        return True
    except Exception as exc:
        logger.debug("vec upsert(%s#%s) failed: %s", table, rowid, exc)
        return False


def delete(conn: sqlite3.Connection, table: str, rowid: int) -> None:
    try:
        conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
    except Exception as exc:
        logger.debug("vec delete(%s#%s) failed: %s", table, rowid, exc)


def delete_all(conn: sqlite3.Connection, table: str) -> None:
    try:
        conn.execute(f"DELETE FROM {table}")
    except Exception as exc:
        logger.debug("vec delete_all(%s) failed: %s", table, exc)


def load_all(
    conn: sqlite3.Connection, table: str, col: str = "embedding"
) -> list[tuple[int, list[float]]]:
    """全量读出 (rowid, 向量)（各缓存场景体量小，整表加载换回内存竞争）。"""
    try:
        rows = conn.execute(f"SELECT rowid, {col} FROM {table}").fetchall()
    except Exception as exc:
        logger.debug("vec load_all(%s) failed: %s", table, exc)
        return []
    return [(int(r[0]), deserialize(r[1])) for r in rows]


def cosine_expr(col: str = "embedding") -> str:
    """余弦相似度 SQL 片段（1 个 ? 占位查询向量）；与原内存 _cosine 同语义。

    零向量时 vec_distance_cosine 返 NULL → COALESCE 回落到 0.0（旧行为：零向量相似度 0）。
    """
    return f"COALESCE(1.0 - vec_distance_cosine({col}, ?), 0.0)"
