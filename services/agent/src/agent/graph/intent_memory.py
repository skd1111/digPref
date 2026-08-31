"""intent_memory —— 意图 Few-Shot 案例库 / 困难样本库 / 操作链路短期记忆（2026-08-31）。

意图识别四层增强 · 动态 Few-Shot + 闭环反馈 + 上下文记忆，三表一库：

    intent_examples      成功路由案例（查询-意图对），动态 Few-Shot 检索源
    intent_hard_samples  困难样本（👎 / HITL 拒绝回流），语义路由负样本库
    intent_recent        按任务页签的近期意图链路（前 3-5 轮操作记忆）

红线：
    - 案例只存改写句 + 细分类型 + 实体**键名**，参数明文不落库
      （延续 evolution 敏感红线：SQL / PII / 凭证绝不进持久层）；
    - DB 与 knowledge.db 同目录（测试经 _isolate chdir 自动隔离）；
    - 全部函数 best-effort：任何故障返空/静默，绝不阻塞意图主链路；
    - embedding 走统一入口（进程内 ONNX），纯本地。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent.config import settings

logger = logging.getLogger("agent.graph.intent_memory")

_LOCK = asyncio.Lock()
_RECENT_KEEP = 5  # 每个任务页签保留的链路条数
_EXAMPLE_POOL_LIMIT = 500  # Few-Shot 检索候选池上限
_FEW_SHOT_TOP_K = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intent_examples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    query_text      TEXT NOT NULL,
    intent_category TEXT NOT NULL,
    entities_json   TEXT NOT NULL DEFAULT '[]',
    vec_json        TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'auto',
    status          TEXT NOT NULL DEFAULT 'neutral',
    ts              TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intent_hard_samples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text    TEXT NOT NULL,
    blocked_route TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    ts            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intent_recent (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intent_recent_task ON intent_recent (task_id, id);
CREATE INDEX IF NOT EXISTS idx_intent_examples_run ON intent_examples (run_id);
"""


def _db_path() -> Path:
    return Path(settings.knowledge_db_path).parent / "intent_memory.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def _use_db() -> AsyncIterator[aiosqlite.Connection]:
    """持锁建连 + 建表的唯一入口。

    陷阱（2026-08-31）：aiosqlite.Connection 被 ``await`` 一次后线程已启动，
    绝不能 ``await connect()`` 后再进 ``async with``（threads can only be
    started once）；必须直接 ``async with aiosqlite.connect(...)``。
    """
    async with _LOCK, aiosqlite.connect(_db_path()) as conn:
        await conn.executescript(_SCHEMA)
        yield conn


# ---- 向量工具 ----------------------------------------------------------------


async def _embed_text(text: str) -> list[float] | None:
    """经统一入口向量化；不可用/零向量返 None（best-effort）。"""
    try:
        from agent.llm.embedding import build_default_embedding_client

        client = build_default_embedding_client()
        if client is None or not await client.health_check():
            return None
        vec = await client.embed(text)
        return vec if any(vec) else None
    except Exception as exc:
        logger.debug("intent_memory embed failed: %s", exc)
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    import math

    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---- 成功路由案例（动态 Few-Shot）---------------------------------------------


async def record_example(
    run_id: str,
    query_text: str,
    intent_category: str,
    entity_keys: list[str],
    *,
    source: str = "auto",
) -> None:
    """登记一条成功路由案例（只存实体键名，参数明文不落库）。"""
    text = (query_text or "").strip()
    if not run_id or not text or not intent_category:
        return
    vec = await _embed_text(text)
    try:
        async with _use_db() as conn:
            await conn.execute(
                "INSERT INTO intent_examples "
                "(run_id, query_text, intent_category, entities_json, vec_json, source, status, ts)"
                " VALUES (?, ?, ?, ?, ?, ?, 'neutral', ?)",
                (
                    run_id,
                    text[:500],
                    intent_category,
                    json.dumps(list(entity_keys), ensure_ascii=False),
                    json.dumps(vec) if vec else "",
                    source,
                    _now(),
                ),
            )
            await conn.commit()
    except Exception as exc:
        logger.debug("intent_memory record_example failed: %s", exc)


async def mark_positive(run_id: str) -> None:
    """👍 信号：该 run 的案例置 positive（检索时优先进 Few-Shot）。"""
    if not run_id:
        return
    try:
        async with _use_db() as conn:
            await conn.execute(
                "UPDATE intent_examples SET status='positive' WHERE run_id=?", (run_id,)
            )
            await conn.commit()
    except Exception as exc:
        logger.debug("intent_memory mark_positive failed: %s", exc)


async def harden_by_run(run_id: str) -> None:
    """👎 信号：该 run 的案例置 negative，且原始查询回流困难样本库。"""
    if not run_id:
        return
    try:
        async with _use_db() as conn:
            cursor = await conn.execute(
                "SELECT query_text FROM intent_examples WHERE run_id=? "
                "ORDER BY id DESC LIMIT 1",
                (run_id,),
            )
            row = await cursor.fetchone()
            await conn.execute(
                "UPDATE intent_examples SET status='negative' WHERE run_id=?", (run_id,)
            )
            if row and str(row[0]).strip():
                await conn.execute(
                    "INSERT INTO intent_hard_samples (query_text, blocked_route, source, ts)"
                    " VALUES (?, '', 'thumbs_down', ?)",
                    (str(row[0])[:500], _now()),
                )
            await conn.commit()
    except Exception as exc:
        logger.debug("intent_memory harden_by_run failed: %s", exc)


async def retrieve_examples(text: str, top_k: int = _FEW_SHOT_TOP_K) -> list[dict[str, Any]]:
    """向量检索最相似的 top_k 条正面/中性案例（负样本排除）。"""
    prompt = (text or "").strip()
    if not prompt:
        return []
    qvec = await _embed_text(prompt)
    if qvec is None:
        return []
    try:
        async with _use_db() as conn:
            cursor = await conn.execute(
                "SELECT query_text, intent_category, status, vec_json FROM intent_examples"
                " WHERE status IN ('positive', 'neutral')"
                " ORDER BY id DESC LIMIT ?",
                (_EXAMPLE_POOL_LIMIT,),
            )
            rows = await cursor.fetchall()
    except Exception as exc:
        logger.debug("intent_memory retrieve_examples failed: %s", exc)
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for query_text, category, status, vec_json in rows:
        try:
            vec = [float(x) for x in json.loads(vec_json)] if vec_json else []
        except (TypeError, ValueError):
            vec = []
        if not vec:
            continue
        score = _cosine(qvec, vec)
        if status == "positive":
            score += 0.02  # 用户点赞过的小幅加权
        scored.append(
            (score, {"query_text": str(query_text), "intent_category": str(category)})
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in scored[:top_k]]


async def compose_intent_system_prompt(text: str) -> str:
    """intent_router 基础模板 + 动态 Few-Shot 案例块（检索失败静默跳过）。"""
    from agent.llm.prompts import load_prompt

    base = load_prompt("intent_router")
    try:
        examples = await retrieve_examples(text)
    except Exception as exc:
        logger.debug("intent_memory fewshot compose failed: %s", exc)
        return base
    if not examples:
        return base
    lines = ["", "# 参考历史案例（过往相似请求的成功路由；仅参考，以当前输入为准）"]
    for ex in examples:
        lines.append(f"- 输入：{ex['query_text'][:80]} → intent_category={ex['intent_category']}")
    return base + "\n" + "\n".join(lines)


# ---- 困难样本（闭环反馈负样本库）-----------------------------------------------


async def add_hard_sample(query_text: str, blocked_route: str = "", source: str = "") -> None:
    """登记困难样本（HITL 拒绝 / 👎 回流）→ 语义路由动态负样本。"""
    text = (query_text or "").strip()
    if not text:
        return
    try:
        async with _use_db() as conn:
            await conn.execute(
                "INSERT INTO intent_hard_samples (query_text, blocked_route, source, ts)"
                " VALUES (?, ?, ?, ?)",
                (text[:500], blocked_route, source, _now()),
            )
            await conn.commit()
    except Exception as exc:
        logger.debug("intent_memory add_hard_sample failed: %s", exc)


async def list_hard_samples(limit: int = 50) -> list[str]:
    """最近的困难样本文本（语义路由合并为全局负样本向量）。"""
    try:
        async with _use_db() as conn:
            cursor = await conn.execute(
                "SELECT query_text FROM intent_hard_samples ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [str(r[0]) for r in rows if str(r[0]).strip()]
    except Exception as exc:
        logger.debug("intent_memory list_hard_samples failed: %s", exc)
        return []


# ---- 操作链路短期记忆（按任务页签）---------------------------------------------


def summarize_analysis(analysis: dict[str, Any]) -> str:
    """把一次意图分析压成一行摘要（进链路记忆 / 注入下轮 prompt）。

    只带细分类型与实体键值短描（脱敏截断），不含参数明文。
    """
    category = str(analysis.get("intent_category") or "")
    intent = str(analysis.get("intent") or "")
    parts = [category or intent]
    entities = analysis.get("entities")
    if isinstance(entities, dict) and entities:
        kvs = [f"{k}={str(v)[:30]}" for k, v in list(entities.items())[:3] if str(v).strip()]
        if kvs:
            parts.append("、".join(kvs))
    return "｜".join(p for p in parts if p)[:160]


async def record_recent(task_id: str, summary: str, keep: int = _RECENT_KEEP) -> None:
    """追加一条近期意图链路（同一任务页签只保留最近 keep 条）。"""
    tid = (task_id or "default").strip() or "default"
    text = (summary or "").strip()
    if not text:
        return
    try:
        async with _use_db() as conn:
            await conn.execute(
                "INSERT INTO intent_recent (task_id, summary, ts) VALUES (?, ?, ?)",
                (tid, text[:200], _now()),
            )
            await conn.execute(
                "DELETE FROM intent_recent WHERE task_id=? AND id NOT IN "
                "(SELECT id FROM intent_recent WHERE task_id=? ORDER BY id DESC LIMIT ?)",
                (tid, tid, keep),
            )
            await conn.commit()
    except Exception as exc:
        logger.debug("intent_memory record_recent failed: %s", exc)


async def recent_chain(task_id: str, limit: int = _RECENT_KEEP) -> list[str]:
    """该任务页签的近期意图链路（旧 → 新）。"""
    tid = (task_id or "default").strip() or "default"
    try:
        async with _use_db() as conn:
            cursor = await conn.execute(
                "SELECT summary FROM intent_recent WHERE task_id=? ORDER BY id DESC LIMIT ?",
                (tid, limit),
            )
            rows = await cursor.fetchall()
        chain = [str(r[0]) for r in rows]
        chain.reverse()  # 查询按 id DESC，翻转回旧 → 新
        return chain
    except Exception as exc:
        logger.debug("intent_memory recent_chain failed: %s", exc)
        return []
