"""Phase 7 V1 · Schema 链接 —— 向量检索选 3-5 张最相关表。

安全红线（CLAUDE.md §2）：
  - 表结构 + 字段注释可能含敏感信息，永不出云（embedding 只走本地服务）

架构师红线（design §4.1）：
  - 金融系统几百张表、几千字段，绝不把全量 Schema 塞给大模型
  - 强制裁剪到 3-5 张最相关表（含中文注释）

V1 升级：
  - 本地 embedding（LocalEmbeddingClient，OpenAI 兼容）+ 余弦相似度向量检索
  - 降级策略：embedding 未配置/不可达时退回关键字评分（V0 逻辑）
  - few-shot 动态选取：从 analysis_tasks 按相似度选 top-3 历史 SQL
  - 表向量持久缓存（2026-09-01）：几百张表的向量化结果落 sqlite-vec
    （schema_link_vec.db，按表文本指纹失效），避免每问重新全量向量化；
    统一入口 agent/vector_store.py，与向量模型端侧闭环。
"""

from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import Any

import aiosqlite

from agent import vector_store as vs
from agent.config import settings
from agent.dataexpert.models import TableSchema

logger = logging.getLogger(__name__)

# 最大选表数（红线：不超过 5 张）
MAX_TABLES = 5

# sqlite-vec 持久层（与 knowledge.db 同目录，测试经 chdir 隔离）
_SCHEMA_VEC_TABLE = "schema_table_vec"


def build_embedding_client() -> Any | None:
    """按统一入口懒构建本地 embedding 客户端（进程内 ONNX 优先，显式配置走 HTTP）。

    模型不可用时客户端 health_check 返 False / 返零向量，
    由调用方检查后退化关键字 —— 功能不中断（与知识库 RAG 检索器同降级哲学）。
    """
    from agent.llm.embedding import build_default_embedding_client

    return build_default_embedding_client()


def _schema_vec_db_path() -> Path:
    return Path(settings.knowledge_db_path).parent / "schema_link_vec.db"


async def _load_schema_vectors(fp: str, expected_count: int) -> list[list[float]] | None:
    """表文本指纹命中且条数吻合 → 按序返回表向量；未命中/损坏返 None（现算）。"""
    try:
        async with aiosqlite.connect(_schema_vec_db_path()) as conn:
            if not await vs.load_extension_async(conn):
                return None
            cursor = await conn.execute("SELECT fp FROM schema_meta WHERE id = 1")
            row = await cursor.fetchone()
            if row is None or str(row[0]) != fp:
                return None
            rows = await conn.execute_fetchall(
                f"SELECT m.idx, v.embedding FROM schema_vec_ref m "
                f"JOIN {_SCHEMA_VEC_TABLE} v ON v.rowid = m.rowid ORDER BY m.idx"
            )
        if len(rows) != expected_count:
            return None
        return [vs.deserialize(emb) for _, emb in rows]
    except Exception as exc:
        logger.debug("schema vectors load failed: %s", exc)
        return None


async def _save_schema_vectors(fp: str, vectors: list[list[float]]) -> None:
    """表向量 + 指纹整体重写（best-effort；失败只损失下次现算）。"""
    if not vectors or not any(any(v) for v in vectors):
        return
    dim = len(vectors[0])
    try:
        async with aiosqlite.connect(_schema_vec_db_path()) as conn:
            if not await vs.load_extension_async(conn):
                return
            await conn.executescript(
                "CREATE TABLE IF NOT EXISTS schema_meta ("
                "  id INTEGER PRIMARY KEY CHECK (id = 1), fp TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS schema_vec_ref ("
                "  rowid INTEGER PRIMARY KEY, idx INTEGER NOT NULL);"
            )

            def _rebuild(raw) -> None:
                current = vs.table_dim(raw, _SCHEMA_VEC_TABLE)
                if current is not None and current != dim:
                    raw.execute(f"DROP TABLE {_SCHEMA_VEC_TABLE}")  # 维度漂移重建
                vs.ensure_vec_table(raw, _SCHEMA_VEC_TABLE, dim)
                vs.delete_all(raw, _SCHEMA_VEC_TABLE)
                raw.execute("DELETE FROM schema_vec_ref")
                for idx, vec in enumerate(vectors):
                    if vs.upsert(raw, _SCHEMA_VEC_TABLE, idx + 1, vec):
                        raw.execute(
                            "INSERT INTO schema_vec_ref(rowid, idx) VALUES (?, ?)", (idx + 1, idx)
                        )
                raw.execute("INSERT OR REPLACE INTO schema_meta(id, fp) VALUES (1, ?)", (fp,))

            await vs.run_async(conn, _rebuild)
            await conn.commit()
    except Exception as exc:
        logger.debug("schema vectors save failed: %s", exc)


async def select_tables(
    question: str,
    schema_cache: list[dict],
    *,
    max_tables: int = MAX_TABLES,
    embedding: Any | None = None,
) -> list[TableSchema]:
    """向量检索选 3-5 张最相关表（含中文注释）。

    V1 策略：
      1. 优先走向量检索（本地 embedding，敏感 schema 永不出云）
      2. 降级：embedding 未配置/不可达时退回关键字评分（V0 逻辑）

    Args:
        question: 用户自然语言问题。
        schema_cache: 数据源的表结构缓存（list[dict]）。
        max_tables: 最大返回表数（默认 5，红线不超过 5）。
        embedding: embedding 客户端（可注入测试替身）；缺省时按 settings 懒构建。

    Returns:
        最相关的 TableSchema 列表（≤ max_tables）。
    """
    max_tables = min(max_tables, MAX_TABLES)

    if not schema_cache:
        return []

    # 尝试向量检索
    client = embedding if embedding is not None else build_embedding_client()
    if client is not None:
        try:
            scored = await _vector_rank(question, schema_cache, client)
            if scored:
                return _to_schemas(scored[:max_tables])
        except Exception as e:
            logger.warning("向量检索失败，降级到关键字评分: %s", e)

    # 降级：关键字评分（V0 逻辑）
    scored = _keyword_rank(question, schema_cache)
    return _to_schemas(scored[:max_tables])


async def _vector_rank(
    question: str,
    schema_cache: list[dict],
    client: Any,
) -> list[tuple[float, dict]]:
    """向量检索排序：本地 embedding + 余弦相似度。

    返空列表表示语义通道缺席（零向量/服务掉线），调用方退化关键字评分。
    """
    # 构建每张表的文本表示（表名 + 注释 + 字段名/注释拼接）
    table_texts: list[str] = []
    for tbl in schema_cache:
        parts = [
            tbl.get("name", ""),
            tbl.get("comment", ""),
        ]
        for col in tbl.get("columns", []):
            parts.append(col.get("name", ""))
            parts.append(col.get("comment", ""))
        table_texts.append(" ".join(p for p in parts if p))

    q_emb = await client.embed(question)
    if not any(q_emb):
        return []

    # 表向量：持久缓存（指纹 = 表文本集合）优先，未命中再现算并回写。
    # 金融系统几百张表，避免每次提问都全量向量化。
    fp = hashlib.sha256("\n".join(table_texts).encode("utf-8")).hexdigest()[:16]
    t_embs = await _load_schema_vectors(fp, len(table_texts))
    if t_embs is None:
        t_embs = await client.embed_batch(table_texts)
        if not t_embs or not any(any(v) for v in t_embs):
            return []
        await _save_schema_vectors(fp, t_embs)

    # 余弦相似度排序（单表向量失败返零向量 → 相似度 0 自然沉底）
    scored: list[tuple[float, dict]] = []
    for i, t_emb in enumerate(t_embs):
        sim = _cosine_similarity(q_emb, t_emb)
        scored.append((sim, schema_cache[i]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_rank(question: str, schema_cache: list[dict]) -> list[tuple[float, dict]]:
    """V0 关键字评分（降级策略）。"""
    scored: list[tuple[float, dict]] = []
    question_lower = question.lower()
    question_chars = set(question_lower)

    for tbl in schema_cache:
        score = 0.0
        tbl_name = (tbl.get("name") or "").lower()
        tbl_comment = (tbl.get("comment") or "").lower()

        # 表名匹配
        if tbl_name and tbl_name in question_lower:
            score += 10.0
        # 表注释匹配
        if tbl_comment:
            overlap = sum(1 for c in tbl_comment if c in question_chars)
            score += overlap * 0.5

        # 字段名/注释匹配
        for col in tbl.get("columns", []):
            col_name = (col.get("name") or "").lower()
            col_comment = (col.get("comment") or "").lower()
            if col_name and col_name in question_lower:
                score += 3.0
            if col_comment:
                overlap = sum(1 for c in col_comment if c in question_chars)
                score += overlap * 0.2

        scored.append((score, tbl))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _to_schemas(scored: list[tuple[float, dict]]) -> list[TableSchema]:
    """将评分结果转换为 TableSchema 列表。"""
    from agent.dataexpert.models import ColumnSchema

    result: list[TableSchema] = []
    for _, tbl in scored:
        columns = [
            ColumnSchema(
                name=c.get("name", ""),
                dtype=c.get("type", c.get("dtype", "")),
                comment=c.get("comment", ""),
            )
            for c in tbl.get("columns", [])
        ]
        result.append(
            TableSchema(
                name=tbl.get("name", ""),
                comment=tbl.get("comment", ""),
                columns=columns,
            )
        )
    return result


# ---- Few-shot 动态选取 -------------------------------------------------------


async def select_few_shot(
    question: str,
    history_tasks: list[dict],
    *,
    max_cases: int = 3,
    embedding: Any | None = None,
) -> list[dict]:
    """从历史分析任务中动态选取最相似的 few-shot 案例。

    V1 策略：
      1. 优先走向量相似度（本地 embedding）
      2. 降级：关键字重叠度

    Args:
        question: 用户问题。
        history_tasks: 历史任务列表（含 name/query_sql 字段）。
        max_cases: 最多返回案例数。
        embedding: embedding 客户端（可注入测试替身）；缺省时按 settings 懒构建。

    Returns:
        最相似的案例列表（含 question + sql）。
    """
    if not history_tasks:
        return []

    # 过滤有效案例（必须有 SQL）
    valid = [t for t in history_tasks if t.get("query_sql")]
    if not valid:
        return []

    # 向量相似度
    client = embedding if embedding is not None else build_embedding_client()
    if client is not None:
        try:
            q_emb = await client.embed(question)
            if q_emb and any(q_emb):
                task_texts = [t.get("name", "") for t in valid]
                t_embs = await client.embed_batch(task_texts)
                if t_embs and len(t_embs) == len(valid) and any(any(v) for v in t_embs):
                    scored = [
                        (_cosine_similarity(q_emb, t_embs[i]), valid[i]) for i in range(len(valid))
                    ]
                    scored.sort(key=lambda x: x[0], reverse=True)
                    return [
                        {"question": t.get("name", ""), "sql": t.get("query_sql", "")}
                        for _, t in scored[:max_cases]
                    ]
        except Exception as e:
            logger.warning("few-shot 向量选取失败，降级: %s", e)

    # 降级：关键字重叠
    question_chars = set(question.lower())
    scored_kw: list[tuple[float, dict]] = []
    for t in valid:
        name = (t.get("name") or "").lower()
        overlap = sum(1 for c in name if c in question_chars)
        scored_kw.append((overlap, t))
    scored_kw.sort(key=lambda x: x[0], reverse=True)

    return [
        {"question": t.get("name", ""), "sql": t.get("query_sql", "")}
        for _, t in scored_kw[:max_cases]
    ]
