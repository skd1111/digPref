"""SemanticRouter —— 意图向量快速路由（semantic-router 模式，2026-08-07）。

借鉴 aurelio-labs/semantic-router：为高频意图预置示例句（utterances），
用户输入经本地 embedding 后与示例句向量做余弦匹配，命中即零 LLM 直出
IntentAnalysis（dict），跳过 analyze_intent 的 LLM 调用；未命中 / embedding
不可用一律静默回退原链路（关键词快速路径 → LLM 结构化分析）。

V2 增强（2026-08-31，意图识别四层增强）：
    - 困难负样本（hard_negatives）：与正样本并列向量化；查询与某路由负样本
      的相似度逼近正样本（差值 < negative_margin）时该路由被拦截回退 LLM
      层——区分「讨论/编写删除脚本」与「实际执行删除」这类高度相似句式。
    - 混合检索：字符 bigram 分词的纯 Python BM25 与向量余弦加权融合，
      缓解企业黑话 / 遗留系统代号 / IP 地址在纯向量空间的失配。
    - 风险分级阈值：路由可自带 threshold；high_risk 路由未显式指定时走
      更高的全局高风险阈值，命中仍强制 need_clarification 二次确认
      （执行层另有 HITL 闸门，路由层只加确认不产生任何写副作用）。
    - 动态负样本：闭环反馈库（👎 / HITL 拒绝）累积的困难样本合并为
      全局拦截向量，随使用自动扩充（见 graph/intent_memory.py）。

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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import settings

logger = logging.getLogger("agent.graph.semantic_route")


# ---- Route 定义 ------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    """一条语义路由：名称 + 示例句 + 命中后直出的 IntentAnalysis 片段。

    hard_negatives：高度相似但意图不同的反例句（讨论/编写/排障类表达），
        与正样本同空间竞争，逼近时拦截本路由回退 LLM 层。
    threshold：显式命中阈值；None 时按风险取全局默认（高风险路由走
        settings.semantic_route_high_risk_threshold）。
    high_risk：高风险路由标记——阈值更高，命中强制追问确认。
    """

    name: str
    utterances: tuple[str, ...]
    analysis: dict[str, Any] = field(default_factory=dict)
    hard_negatives: tuple[str, ...] = ()
    threshold: float | None = None
    high_risk: bool = False


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
    Route(
        name="db_query",
        utterances=(
            "查询订单表",
            "查一下订单表的数据",
            "查询用户表最近一周的记录",
            "看看订单表里有多少条数据",
            "从订单表查询昨天的订单",
        ),
        hard_negatives=(
            "帮我写一个查询订单表的 Python 脚本",
            "订单表查询接口总是超时怎么优化",
            "写一段查询订单表的 SQL 但先不要执行",
        ),
        analysis={
            "intent": "query",
            "intent_category": "data_query",
            "need_tool": True,
            "need_clarification": False,
            "risk_level": "low",
            "confidence": 0.85,
            "reason": "语义路由命中：数据库只读查询",
        },
    ),
    Route(
        name="db_drop",
        utterances=(
            "删掉订单表",
            "清空订单表",
            "把订单表删除",
            "删除订单表里的全部数据",
            "把用户表清空",
        ),
        hard_negatives=(
            "帮我写一个删除订单表的脚本",
            "订单表删了怎么恢复",
            "写一段删除重复记录的 SQL 给我看看",
            "为什么订单表删不掉",
        ),
        analysis={
            "intent": "mutate",
            "intent_category": "task_execution",
            "need_tool": True,
            "need_clarification": True,
            "risk_level": "critical",
            "confidence": 0.7,
            "reason": "语义路由命中：数据库删除/清空（高风险，需二次确认）",
            "clarification_message": "这是不可逆的删除操作。请确认：要删除/清空的是哪张表？目标库是哪个？",
        },
        high_risk=True,
    ),
    Route(
        name="ssh_execute",
        utterances=(
            "连上 10.0.0.5 重启服务",
            "登录 192.168.1.10 执行重启命令",
            "SSH 到生产服务器上重启网关",
            "连接内网服务器 172.16.0.3 停掉旧进程",
        ),
        hard_negatives=(
            "帮我写一个 SSH 连接的 Python 脚本",
            "SSH 连接超时一般是什么原因",
            "写一段登录服务器查日志的脚本别执行",
        ),
        analysis={
            "intent": "mutate",
            "intent_category": "task_execution",
            "need_tool": True,
            "need_clarification": True,
            "risk_level": "high",
            "confidence": 0.7,
            "reason": "语义路由命中：远程服务器执行（高风险，需二次确认）",
            "clarification_message": "即将在远程服务器上执行操作。请确认目标主机与要执行的命令。",
        },
        high_risk=True,
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
    """示例句 + 负样本 + 模型名的指纹 —— 任一变化即缓存失效。"""
    payload = json.dumps(
        [model]
        + [[r.name, list(r.utterances), list(r.hard_negatives)] for r in routes],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_path() -> Path:
    return Path(settings.knowledge_db_path).parent / "semantic_route_cache.json"


# ---- BM25（纯 Python，字符 bigram 分词）--------------------------------------

# 字母数字/代号/IP/端点整段切出（企业黑话、遗留系统代号、10.0.0.5 之类）
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:%-]*")
# 连续中文串 → 串内做字符 bigram（中文无空格分词，bigram 对短查询足够稳）
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    """粗粒度分词：ASCII 代号整段 + 中文串内字符 bigram（单字串保留单字）。"""
    lowered = (text or "").lower()
    tokens = _ASCII_TOKEN_RE.findall(lowered)
    for run in _CJK_RUN_RE.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def _bm25_route_scores(
    query_tokens: list[str], routes: tuple[Route, ...]
) -> dict[str, float]:
    """每个路由的 BM25 分 = 其示例句中的最高分（语料 = 全部正样本句）。"""
    if not query_tokens:
        return {}
    docs: list[tuple[str, list[str]]] = [
        (r.name, _tokenize(u)) for r in routes for u in r.utterances
    ]
    n_docs = len(docs)
    if not n_docs:
        return {}
    df: dict[str, int] = {}
    for _, toks in docs:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    avg_len = sum(len(toks) for _, toks in docs) / n_docs or 1.0
    k1, b = 1.5, 0.75

    best: dict[str, float] = {}
    for name, toks in docs:
        if not toks:
            continue
        score = 0.0
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        for q in set(query_tokens):
            f = tf.get(q, 0)
            if not f:
                continue
            idf = math.log(1 + (n_docs - df[q] + 0.5) / (df[q] + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(toks) / avg_len))
        if score > best.get(name, 0.0):
            best[name] = score
    return best


# ---- 动态困难样本（闭环反馈库）-----------------------------------------------


async def _load_hard_samples(limit: int = 50) -> list[str]:
    """读取闭环反馈累积的困难样本文本；任何故障返空（快速路径不得影响主链路）。"""
    try:
        from agent.graph.intent_memory import list_hard_samples

        return await list_hard_samples(limit=limit)
    except Exception as exc:
        logger.debug("semantic_route load hard samples failed: %s", exc)
        return []


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
        self._vectors: list[tuple[str, list[float]]] | None = None  # 正样本 (route, vec)
        self._neg_vectors: list[tuple[str, list[float]]] | None = None  # 负样本 (route, vec)
        self._dyn_neg_vectors: list[list[float]] = []  # 闭环反馈全局负样本
        self._dyn_fp = ""
        self._unavailable = False

    # ---- 向量准备 ---------------------------------------------------------

    async def _get_embedding_client(self) -> Any | None:
        if self._embedding is not None:
            return self._embedding
        # 统一入口（2026-08-31）：进程内 ONNX 优先，显式配置时走外置 HTTP 端点；
        # 模型不可用时 health_check 返 False，_ensure_vectors 标记静默回退。
        from agent.llm.embedding import build_default_embedding_client

        self._embedding = build_default_embedding_client()
        return self._embedding

    async def _ensure_vectors(self) -> bool:
        """确保示例句向量就绪（缓存优先）；不可用返 False。"""
        if self._unavailable:
            return False
        if self._vectors is not None and self._neg_vectors is not None:
            await self._refresh_dynamic_negatives()
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
                    self._neg_vectors = [
                        (str(name), [float(x) for x in vec])
                        for name, vec in data.get("negative_vectors", [])
                    ]
                    if self._vectors:
                        await self._refresh_dynamic_negatives()
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
        neg_texts = [n for r in self._routes for n in r.hard_negatives]
        try:
            vecs = await client.embed_batch(texts + neg_texts)
        except Exception as exc:  # 向量化失败 → 静默回退原链路
            logger.debug("semantic_route embed_batch failed: %s", exc)
            self._unavailable = True
            return False
        if len(vecs) != len(texts) + len(neg_texts) or any(not any(v) for v in vecs):
            self._unavailable = True  # 全零向量 = embedding 服务异常
            return False

        pairs: list[tuple[str, list[float]]] = []
        idx = 0
        for r in self._routes:
            for _ in r.utterances:
                pairs.append((r.name, vecs[idx]))
                idx += 1
        neg_pairs: list[tuple[str, list[float]]] = []
        for r in self._routes:
            for _ in r.hard_negatives:
                neg_pairs.append((r.name, vecs[idx]))
                idx += 1
        self._vectors = pairs
        self._neg_vectors = neg_pairs
        try:
            cache.write_text(
                json.dumps(
                    {
                        "fingerprint": fp,
                        "vectors": pairs,
                        "negative_vectors": neg_pairs,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as exc:  # 缓存写失败不影响运行
            logger.debug("semantic_route cache write failed: %s", exc)
        await self._refresh_dynamic_negatives()
        return True

    async def _refresh_dynamic_negatives(self) -> None:
        """增量刷新闭环反馈困难样本向量（样本集指纹变化才重新向量化）。"""
        client = self._embedding
        if client is None:
            return
        try:
            texts = await _load_hard_samples()
        except Exception as exc:  # 反馈库故障不得影响路由主链路
            logger.debug("semantic_route load hard samples raised: %s", exc)
            texts = []
        fp = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()[:16]
        if fp == self._dyn_fp:
            return
        if not texts:
            self._dyn_neg_vectors = []
            self._dyn_fp = fp
            return
        try:
            vecs = await client.embed_batch(texts)
        except Exception as exc:
            logger.debug("semantic_route dynamic negatives embed failed: %s", exc)
            return
        if len(vecs) != len(texts) or any(not any(v) for v in vecs):
            return
        self._dyn_neg_vectors = vecs
        self._dyn_fp = fp

    # ---- 阈值 ---------------------------------------------------------------

    def _effective_threshold(self, route: Route) -> float:
        if self._threshold is not None:
            return self._threshold
        if route.threshold is not None:
            return route.threshold
        if route.high_risk:
            return settings.semantic_route_high_risk_threshold
        return settings.semantic_route_threshold

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

        route_map = {r.name: r for r in self._routes}

        # 1) 正样本余弦 → 每路由最高分
        pos_best: dict[str, float] = {}
        for name, vec in self._vectors or []:
            score = _cosine(qvec, vec)
            if score > pos_best.get(name, 0.0):
                pos_best[name] = score
        if not pos_best:
            return None
        best_name = max(pos_best, key=lambda n: pos_best[n])
        best_score = pos_best[best_name]

        # 2) 困难负样本竞争：查询更像「讨论/编写/排障」反例时拦截回退 LLM 层
        margin = settings.semantic_route_negative_margin
        neg_best = max(
            (_cosine(qvec, vec) for name, vec in (self._neg_vectors or []) if name == best_name),
            default=0.0,
        )
        dyn_best = max((_cosine(qvec, vec) for vec in self._dyn_neg_vectors), default=0.0)
        if neg_best >= best_score - margin or dyn_best >= best_score - margin:
            try:
                from agent.observability.cot_log import cot as cot_log

                cot_log(
                    "semantic_route.negative_block",
                    text=prompt,
                    route=best_name,
                    pos_score=round(best_score, 4),
                    neg_score=round(max(neg_best, dyn_best), 4),
                )
            except Exception:  # 日志失败不影响路由
                pass
            return None

        # 3) BM25 混合检索：关键词信号与向量融合（无关键词信号时保持纯向量）
        bm25_scores = _bm25_route_scores(_tokenize(prompt), self._routes)
        bm25_max = max(bm25_scores.values(), default=0.0)
        weight = settings.semantic_route_hybrid_weight
        fused_best_name, fused_best_score = best_name, best_score
        if bm25_max > 0 and 0 < weight < 1:
            fused: dict[str, float] = {}
            for name in pos_best:
                fused[name] = (
                    pos_best[name] * (1 - weight)
                    + (bm25_scores.get(name, 0.0) / bm25_max) * weight
                )
            fused_best_name = max(fused, key=lambda n: fused[n])
            fused_best_score = fused[fused_best_name]
            # 融合可能翻盘到更贴合关键词的路由；翻盘路由需自带正样本向量支撑
            if fused_best_name != best_name and fused_best_name in pos_best:
                best_name = fused_best_name
                best_score = pos_best[best_name]

        threshold = self._effective_threshold(route_map[best_name])
        final_score = fused_best_score if best_name == fused_best_name else best_score
        try:
            from agent.observability.cot_log import cot as cot_log

            cot_log(
                "semantic_route.score",
                text=prompt,
                best_route=best_name,
                best_score=round(final_score, 4),
                vector_score=round(best_score, 4),
                bm25_score=round(bm25_scores.get(best_name, 0.0), 4),
                threshold=threshold,
                hit=bool(final_score >= threshold and best_name in route_map),
            )
        except Exception:  # 日志失败不影响路由
            pass
        if final_score < threshold or best_name not in route_map:
            return None

        route = route_map[best_name]
        analysis = dict(route.analysis)
        analysis["rewritten_query"] = prompt
        analysis["_route"] = best_name
        analysis["_route_score"] = round(final_score, 4)
        analysis.setdefault("backend", "semantic_route")
        # 高风险路由：命中即强制追问确认（置信度下调），执行层另有 HITL 闸门
        if route.high_risk:
            analysis["need_clarification"] = True
            analysis["confidence"] = min(float(analysis.get("confidence", 0.9)), 0.75)
            analysis.setdefault(
                "clarification_message", "这是高风险操作，请确认目标与操作内容后再执行。"
            )
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
