"""SemanticRouter —— 意图向量快速路由（semantic-router 模式，2026-08-07）。

借鉴 aurelio-labs/semantic-router：为高频意图预置示例句（utterances），
用户输入经本地 embedding 后与示例句向量做余弦匹配，命中即零 LLM 直出
IntentAnalysis（dict），跳过 analyze_intent 的 LLM 调用；未命中 / embedding
不可用一律静默回退原链路（关键词快速路径 → LLM 结构化分析）。

安全约束：
    - embedding 只走本地（LocalEmbeddingClient），不触及云端 —— 与
      _LOCAL_ONLY_TASKS 本地红线一致；
    - 只读路由：不产生任何写操作 / HITL / 审计副作用；
    - 示例句向量缓存落本地 JSON（与 knowledge.db 同目录，测试自动隔离）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import settings

logger = logging.getLogger("agent.graph.semantic_route")


# ---- Route 定义 ------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    """一条语义路由：名称 + 示例句 + 命中后直出的 IntentAnalysis 片段。"""

    name: str
    utterances: tuple[str, ...]
    analysis: dict[str, Any] = field(default_factory=dict)


# 预置路由（高频 + 确定性场景；analysis 字段与 IntentAnalysis.to_dict 对齐）
DEFAULT_ROUTES: tuple[Route, ...] = (
    Route(
        name="chitchat",
        utterances=(
            "你好",
            "您好",
            "嗨",
            "在吗",
            "你是谁",
            "介绍一下你自己",
            "谢谢",
            "谢谢你",
            "多谢",
            "辛苦了",
            "再见",
            "拜拜",
            "好的谢谢",
        ),
        analysis={
            "intent": "chitchat",
            "intent_category": "chat",
            "need_tool": False,
            "need_clarification": False,
            "risk_level": "low",
            "confidence": 0.9,
            "reason": "语义路由命中：闲聊/问候",
            "canned_response": ("你好，我是 EAIDE 企业 AI 助理。告诉我你想查询或操作哪个系统吧。"),
        },
    ),
    Route(
        name="time_query",
        utterances=(
            "今天几号",
            "今天是几号",
            "今天几月几号",
            "今天星期几",
            "现在几点了",
            "现在几点",
            "当前时间",
            "今天的日期",
            "农历初几",
            "今天农历多少",
            "今天是农历几号",
        ),
        analysis={
            "intent": "query",
            "intent_category": "data_query",
            "need_tool": True,
            "need_clarification": False,
            "risk_level": "low",
            "confidence": 0.9,
            "reason": "语义路由命中：时间/日期查询（datetime_now）",
        },
    ),
)


# ---- 工具函数 ---------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _routes_fingerprint(routes: tuple[Route, ...], model: str) -> str:
    """示例句 + 模型名的指纹 —— 任一变化即缓存失效。"""
    payload = json.dumps(
        [model] + [[r.name, list(r.utterances)] for r in routes],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_path() -> Path:
    return Path(settings.knowledge_db_path).parent / "semantic_route_cache.json"


# ---- 路由器 -----------------------------------------------------------------


class SemanticIntentRouter:
    """零 LLM 向量预路由。embedding 客户端可注入（测试用替身）。"""

    def __init__(
        self,
        routes: tuple[Route, ...] = DEFAULT_ROUTES,
        embedding: Any | None = None,
        *,
        threshold: float | None = None,
    ) -> None:
        self._routes = routes
        self._embedding = embedding
        self._threshold = threshold
        self._vectors: list[tuple[str, list[float]]] | None = None  # (route_name, vec)
        self._unavailable = False

    # ---- 向量准备 ---------------------------------------------------------

    async def _get_embedding_client(self) -> Any | None:
        if self._embedding is not None:
            return self._embedding
        if not settings.local_embedding_base_url:
            return None
        from agent.llm.embedding import LocalEmbeddingClient

        self._embedding = LocalEmbeddingClient(
            base_url=settings.local_embedding_base_url,
            model=settings.local_embedding_model or "bge-small-zh-v1.5",
            dimensions=settings.local_embedding_dim,
        )
        return self._embedding

    async def _ensure_vectors(self) -> bool:
        """确保示例句向量就绪（缓存优先）；不可用返 False。"""
        if self._unavailable:
            return False
        if self._vectors is not None:
            return True
        client = await self._get_embedding_client()
        if client is None:
            self._unavailable = True
            return False

        model = str(getattr(client, "model", "") or "local")
        fp = _routes_fingerprint(self._routes, model)
        cache = _cache_path()
        try:
            if cache.exists():
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("fingerprint") == fp:
                    self._vectors = [
                        (str(name), [float(x) for x in vec])
                        for name, vec in data.get("vectors", [])
                    ]
                    if self._vectors:
                        return True
        except Exception as exc:  # 缓存损坏重建即可
            logger.debug("semantic_route cache load failed: %s", exc)

        # 现算：先探活，避免无谓等待
        try:
            healthy = await client.health_check()
        except Exception:  # 探活失败视为不可用
            healthy = False
        if not healthy:
            self._unavailable = True
            return False

        texts = [u for r in self._routes for u in r.utterances]
        try:
            vecs = await client.embed_batch(texts)
        except Exception as exc:  # 向量化失败 → 静默回退原链路
            logger.debug("semantic_route embed_batch failed: %s", exc)
            self._unavailable = True
            return False
        if len(vecs) != len(texts) or any(not any(v) for v in vecs):
            self._unavailable = True  # 全零向量 = embedding 服务异常
            return False

        pairs: list[tuple[str, list[float]]] = []
        idx = 0
        for r in self._routes:
            for _ in r.utterances:
                pairs.append((r.name, vecs[idx]))
                idx += 1
        self._vectors = pairs
        try:
            cache.write_text(
                json.dumps(
                    {"fingerprint": fp, "vectors": pairs},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as exc:  # 缓存写失败不影响运行
            logger.debug("semantic_route cache write failed: %s", exc)
        return True

    # ---- 路由 ---------------------------------------------------------------

    async def route(self, text: str) -> dict[str, Any] | None:
        """命中返 IntentAnalysis dict（含 _route 来源标记）；否则返 None。"""
        if not getattr(settings, "semantic_route_enabled", False):
            return None
        prompt = (text or "").strip()
        if not prompt:
            return None
        if not await self._ensure_vectors():
            return None

        client = await self._get_embedding_client()
        if client is None:
            return None
        try:
            qvec = await client.embed(prompt)
        except Exception as exc:  # 查询向量化失败 → 回退 LLM 分析
            logger.debug("semantic_route query embed failed: %s", exc)
            return None
        if not any(qvec):
            return None

        threshold = (
            self._threshold if self._threshold is not None else settings.semantic_route_threshold
        )
        best_name, best_score = "", 0.0
        route_map = {r.name: r for r in self._routes}
        for name, vec in self._vectors or []:
            score = _cosine(qvec, vec)
            if score > best_score:
                best_name, best_score = name, score
        if best_score < threshold or best_name not in route_map:
            return None

        analysis = dict(route_map[best_name].analysis)
        analysis["rewritten_query"] = prompt
        analysis["_route"] = best_name
        analysis["_route_score"] = round(best_score, 4)
        analysis.setdefault("backend", "semantic_route")
        return analysis


# ---- 单例 --------------------------------------------------------------------

_default_router: SemanticIntentRouter | None = None


def get_semantic_router() -> SemanticIntentRouter:
    global _default_router
    if _default_router is None:
        _default_router = SemanticIntentRouter()
    return _default_router


def reset_semantic_router() -> None:
    """测试隔离用（conftest._isolate 可调用）。"""
    global _default_router
    _default_router = None
