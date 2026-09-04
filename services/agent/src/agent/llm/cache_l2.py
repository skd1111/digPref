"""L2 语义缓存（Phase 2C V3）—— embedding cosine similarity。

命中条件：prompt embedding 与已缓存条目 embedding 的 cosine sim >= threshold。
用途：用户问"查订单"和"看看订单"语义相近，第二次直接返回，省一次 LLM 调用。

依赖：Phase 4 本地 embedding 模型（bge-small-zh / sentence-transformers）。Phase 4 未上线前**默认禁用**（enable=False 走 pass-through）。

设计要点：
    - `enable` 开关：默认 False。Settings → 模型管理 提供 toggle。
    - `embed_fn` 注入：测试可传 mock（hash → 64 维向量）；生产用 sentence-transformers。
    - 阈值 0.92 / TTL 24h（按 SCHEDULE §3.1 估算）
    - 命中时同样返回字符串 + sources_referenced（与 L1 一致）
    - 存储：双层 —— 进程内 dict（快路径）+ SQLite 持久层（复用 router.db，
      l2_cache 表 + l2_cache_vec sqlite-vec 虚拟表，重启后仍可语义命中）。

CLAUDE.md §2 红线：
    - 不读敏感上下文：`biznav_extract` / `repair` / `intent` 等 _LOCAL_ONLY_TASKS 任务
      不写 L2（敏感上下文不缓存）。
    - 命中不算 API 成本（同 L1.cache_hit）。

不在 V3 内（V3.5 补）：
    - 真接 Phase 4 bge-small-zh 推理
    - LRU 淘汰（V3 用 TTL 24h 单维度淘汰）
    - 多模态 embedding（图像 / 代码）
"""

from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
import time
from collections.abc import Callable

from agent import vector_store as vs

# _LOCAL_ONLY_TASKS 不写 L2（敏感上下文不缓存）
from agent.llm.router import _LOCAL_ONLY_TASKS

logger = logging.getLogger("agent.llm.cache_l2")

_L2_TABLE = "l2_cache"
_L2_VEC_TABLE = "l2_cache_vec"


def _l2_db_path() -> str:
    """持久层库路径（复用 router.db，相对路径随测试 chdir 隔离）。"""
    from agent.config import settings

    return settings.llm_router_db_path


def _l2_connect() -> sqlite3.Connection | None:
    """建连 + 加载 sqlite-vec + 建表；任一失败返 None（内存层不受影响）。"""
    try:
        conn = sqlite3.connect(_l2_db_path(), timeout=5)
        if not vs.load_extension(conn):
            conn.close()
            return None
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_L2_TABLE} ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  key TEXT NOT NULL UNIQUE,"
            "  model TEXT NOT NULL,"
            "  text TEXT NOT NULL,"
            "  expires_at REAL NOT NULL)"
        )
        return conn
    except Exception as exc:
        logger.debug("l2_cache db connect failed: %s", exc)
        return None


def mock_embed(prompt: str, dim: int = 64) -> list[float]:
    """Mock embedding（Phase 4 未上线时的默认实现）。

    用 sha256 哈希展开成伪随机向量 —— 不会触发 LLM，但相似 prompt 会得到相似向量
    （因为相同 prompt → 相同 hash → 相同向量）。**仅供 PoC / 测试**。
    """
    raw = hashlib.sha256(prompt.encode("utf-8")).digest()
    vec = []
    for i in range(dim):
        b = raw[i % len(raw)]
        vec.append((b / 127.5) - 1.0)  # 归一到 [-1, 1]
    return vec


def cosine_sim(a: list[float], b: list[float]) -> float:
    """cosine similarity，[-1, 1]。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class L2Cache:
    """embedding cosine similarity 语义缓存。

    Args:
        enable: 是否启用（默认 False，Phase 4 上线后 Settings 切换）
        threshold: 命中阈值（默认 0.92，SCHEDULE §3.1 估算）
        ttl_sec: TTL 秒数（默认 86400 = 24h）
        embed_fn: 文本 → 向量；默认 mock_embed
        dim: 向量维度（mock 64 / 生产 384 for bge-small-zh）

    命中：cosine_sim >= threshold
    miss：threshold 不达 / TTL 过期 / 已禁用
    """

    def __init__(
        self,
        *,
        enable: bool = False,
        threshold: float = 0.92,
        ttl_sec: float = 86400.0,
        embed_fn: Callable[[str], list[float]] | None = None,
        dim: int = 64,
    ) -> None:
        self._enable = enable
        self._threshold = float(threshold)
        self._ttl = float(ttl_sec)
        self._embed = embed_fn or (lambda p: mock_embed(p, dim=dim))
        # key = sha256(model + prompt); value = (embedding, text, expires_at)
        self._store: dict[str, tuple[list[float], str, float]] = {}
        self.hits = 0
        self.misses = 0

    @property
    def enable(self) -> bool:
        return self._enable

    def set_enable(self, enable: bool) -> None:
        """运行时 toggle（Phase 4 上线后 Settings → 模型管理 UI 调此）。"""
        self._enable = enable

    @staticmethod
    def _make_key(model: str, prompt: str) -> str:
        normalized = " ".join(prompt.split())
        raw = f"{model}\x00{normalized}".encode()
        return hashlib.sha256(raw).hexdigest()

    def get(self, model: str, prompt: str) -> str | None:
        if not self._enable:
            self.misses += 1
            return None
        key = self._make_key(model, prompt)
        entry = self._store.get(key)
        if entry is None:
            # 即使 prompt 完全一致也要尝试 embedding 匹配（语义缓存）
            target = self._embed(prompt)
            best_key: str | None = None
            best_sim = 0.0
            for k, (emb, _text, expires) in self._store.items():
                if expires < time.monotonic():
                    continue  # 过期跳过
                sim = cosine_sim(target, emb)
                if sim > best_sim:
                    best_sim = sim
                    best_key = k
            if best_sim >= self._threshold and best_key is not None:
                self.hits += 1
                return self._store[best_key][1]
            # 内存未命中 → 查持久层（重启后的条目只在库里）
            db_hit = self._db_lookup(model, target)
            if db_hit is not None:
                self.hits += 1
                return db_hit
            self.misses += 1
            return None
        # 精确命中
        emb, text, expires = entry
        if expires < time.monotonic():
            del self._store[key]
            self.misses += 1
            return None
        self.hits += 1
        return text

    def put(
        self,
        model: str,
        prompt: str,
        text: str,
        *,
        task_kind: str | None = None,
    ) -> None:
        """写入缓存。

        敏感任务（_LOCAL_ONLY_TASKS）一律不缓存 —— 与 LMRouter 红线对齐。
        """
        if not self._enable:
            return
        if task_kind is not None and task_kind in _LOCAL_ONLY_TASKS:
            return
        if task_kind == "intent":
            return  # 防御纵深：intent 不缓存
        key = self._make_key(model, prompt)
        emb = self._embed(prompt)
        self._store[key] = (emb, text, time.monotonic() + self._ttl)
        self._db_persist(key, model, prompt, text, emb)

    # ---- 持久层（router.db + sqlite-vec，best-effort）-------------------------

    def _db_persist(self, key: str, model: str, prompt: str, text: str, emb: list[float]) -> None:
        """写持久层：条目 + 向量（维度漂移时重建 vec 表）。"""
        if not emb or not any(emb):
            return
        conn = _l2_connect()
        if conn is None:
            return
        try:
            vs.ensure_vec_table(conn, _L2_VEC_TABLE, len(emb))
            conn.execute(
                f"INSERT INTO {_L2_TABLE} (key, model, text, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET text=excluded.text, "
                "expires_at=excluded.expires_at",
                (key, model, text, time.time() + self._ttl),
            )
            row_id = conn.execute(f"SELECT id FROM {_L2_TABLE} WHERE key = ?", (key,)).fetchone()[0]
            vs.upsert(conn, _L2_VEC_TABLE, int(row_id), emb)
            conn.commit()
        except Exception as exc:
            logger.debug("l2_cache db persist failed: %s", exc)
        finally:
            conn.close()

    def _db_lookup(self, model: str, target: list[float]) -> str | None:
        """持久层语义检索：同 model 未过期条目中相似度最高且达阈者；并回填内存层。"""
        conn = _l2_connect()
        if conn is None:
            return None
        try:
            if vs.table_dim(conn, _L2_VEC_TABLE) != len(target):
                return None
            row = conn.execute(
                f"SELECT c.text, c.expires_at, {vs.cosine_expr('v.embedding')} AS sim "
                f"FROM {_L2_TABLE} c JOIN {_L2_VEC_TABLE} v ON v.rowid = c.id "
                "WHERE c.model = ? ORDER BY sim DESC LIMIT 1",
                (vs.serialize(target), model),
            ).fetchone()
            if row is None:
                return None
            text, expires_at, sim = str(row[0]), float(row[1]), float(row[2])
            if sim < self._threshold or expires_at <= time.time():
                return None
            # 回填内存层（TTL 按剩余墙钟时间折回 monotonic）
            key = hashlib.sha256(text.encode("utf-8")).hexdigest()  # 占位，不参与精确命中
            self._store.setdefault(
                key, (target, text, time.monotonic() + max(expires_at - time.time(), 0.0))
            )
            return text
        except Exception as exc:
            logger.debug("l2_cache db lookup failed: %s", exc)
            return None
        finally:
            conn.close()

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": len(self._store)}

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0
        conn = _l2_connect()
        if conn is not None:
            try:
                conn.execute(f"DELETE FROM {_L2_TABLE}")
                vs.delete_all(conn, _L2_VEC_TABLE)
                conn.commit()
            except Exception as exc:
                logger.debug("l2_cache db clear failed: %s", exc)
            finally:
                conn.close()
