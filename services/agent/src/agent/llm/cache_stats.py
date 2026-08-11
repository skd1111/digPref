"""缓存命中率统计（Phase 17 V0）—— GET /router/cache-stats 的数据源。

口径：
    1. L1 进程内计数器（hits / misses / hit_rate / size）—— 实时；
    2. L3 幂等只读工具结果缓存计数器 —— 实时；
    3. routing_decisions 表历史 cache_hit 比例 —— 全量 + 最近 24h 双窗口。

范围裁剪（2026-08-10）：本地不自建 RAG（未来走外部 RAG 接口，本地检索用
grep），L2 语义缓存与 embedding/检索缓存不在范围内。

设计文档：docs/design/phase-17-cache-hit-rate.md §5（观测指标）
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)


def _decision_cache_stats() -> dict[str, Any]:
    """从 router.db routing_decisions 聚合 cache_hit 比例。失败返回零值不抛。"""
    from agent.llm.metrics import _router_db_path

    out: dict[str, Any] = {
        "total_decisions": 0,
        "cache_hits": 0,
        "cache_hit_rate": 0.0,
        "recent_24h_decisions": 0,
        "recent_24h_cache_hits": 0,
    }
    try:
        conn = sqlite3.connect(str(_router_db_path()), timeout=5)
        try:
            cur = conn.execute("SELECT COUNT(*), COALESCE(SUM(cache_hit), 0) FROM routing_decisions")
            total, hits = cur.fetchone()
            out["total_decisions"] = int(total or 0)
            out["cache_hits"] = int(hits or 0)
            if total:
                out["cache_hit_rate"] = round(hits / total, 4)
            cutoff_ms = int((time.time() - 86400) * 1000)
            cur = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(cache_hit), 0) FROM routing_decisions "
                "WHERE created_at >= ?",
                (cutoff_ms,),
            )
            r_total, r_hits = cur.fetchone()
            out["recent_24h_decisions"] = int(r_total or 0)
            out["recent_24h_cache_hits"] = int(r_hits or 0)
        finally:
            conn.close()
    except Exception as e:
        # 首次启动 / 测试隔离环境下表可能尚未建 —— 降级零值，不阻塞统计端点
        logger.debug("cache_stats_decisions_failed err=%s", e)
    return out


def get_cache_stats() -> dict[str, Any]:
    """汇总分层缓存命中率（供 /router/cache-stats 端点 / 前端模型管理页）。

    范围裁剪（2026-08-10）：本地不自建 RAG（未来走外部 RAG 接口，本地检索
    用 grep），故无 embedding/检索缓存层；L2 语义缓存依赖本地向量，同步搁置。
    """
    from agent.llm.router import get_l1_cache, is_l1_cache_enabled
    from agent.llm.tool_cache import get_tool_cache, is_tool_cache_enabled

    l1 = get_l1_cache()
    l3 = get_tool_cache()
    return {
        "l1_exact": {
            "enabled": is_l1_cache_enabled(),
            "hits": l1.hits,
            "misses": l1.misses,
            "hit_rate": round(l1.hit_rate, 4),
            "size": l1.size,
        },
        "l3_tool_result": {
            "enabled": is_tool_cache_enabled(),
            "hits": l3.hits,
            "misses": l3.misses,
            "hit_rate": round(l3.hit_rate, 4),
            "size": l3.size,
        },
        # L2 语义缓存依赖本地向量检索 —— 本地不自建 RAG，暂不启用
        "l2_semantic": {"enabled": False, "hits": 0, "misses": 0, "hit_rate": 0.0},
        "decisions": _decision_cache_stats(),
    }
