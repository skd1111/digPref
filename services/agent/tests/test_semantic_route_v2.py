"""语义路由 V2 测试（2026-08-31）：困难负样本 / BM25 混合 / 风险分级阈值 / 动态负样本。"""

from __future__ import annotations

from agent.config import settings
from agent.graph import semantic_route as sr
from agent.graph.semantic_route import (
    Route,
    SemanticIntentRouter,
    _bm25_route_scores,
    _tokenize,
)

# ---- 确定性伪 embedding：精确文本 → 固定向量 ----------------------------------


class _DictEmbedding:
    """精确文本映射向量；未登记文本返默认向量（负半轴）。"""

    model = "fake-dict"

    def __init__(self, mapping: dict[str, list[float]], default=None) -> None:
        self.mapping = mapping
        self.default = default or [-1.0, 0.0, 0.0, 0.0]

    def _vec(self, text: str) -> list[float]:
        return self.mapping.get(text, self.default)

    async def health_check(self) -> bool:
        return True

    async def embed(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


def _router(routes, mapping, **kw) -> SemanticIntentRouter:
    return SemanticIntentRouter(routes, embedding=_DictEmbedding(mapping), **kw)


# ---- 困难负样本 ---------------------------------------------------------------


class TestHardNegatives:
    async def test_positive_hit_when_negative_far(self, monkeypatch):
        """正样本向量贴近、负样本远离 → 正常命中。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        routes = (
            Route(
                name="db_drop",
                utterances=("删掉订单表",),
                hard_negatives=("帮我写一个删除订单表的脚本",),
                analysis={"intent": "mutate", "risk_level": "critical"},
            ),
        )
        mapping = {
            "删掉订单表": [0.0, 0.0, 0.0, 1.0],
            "帮我写一个删除订单表的脚本": [0.0, 0.0, 0.5, 0.87],
        }
        out = await _router(routes, mapping).route("删掉订单表")
        assert out is not None
        assert out["_route"] == "db_drop"

    async def test_negative_blocks_when_closer(self, monkeypatch):
        """查询更像负样本（讨论/编写类）→ 拦截回退，返 None。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        routes = (
            Route(
                name="db_drop",
                utterances=("删掉订单表",),
                hard_negatives=("帮我写一个删除订单表的脚本",),
                analysis={"intent": "mutate", "risk_level": "critical"},
            ),
        )
        mapping = {
            "删掉订单表": [0.0, 0.0, 0.0, 1.0],
            "帮我写一个删除订单表的脚本": [0.0, 0.0, 0.5, 0.87],
        }
        out = await _router(routes, mapping).route("帮我写一个删除订单表的脚本")
        assert out is None

    async def test_negative_margin_tunable(self, monkeypatch):
        """裕度收紧到 0 后，与负样本仅差 0.01 的边缘查询放行（默认 0.02 会拦截）。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "semantic_route_negative_margin", 0.0)
        routes = (
            Route(
                name="db_drop",
                utterances=("删掉订单表",),
                hard_negatives=("帮我写一个删除订单表的脚本",),
                analysis={"intent": "mutate"},
            ),
        )
        # 查询与正样本同向量（cos 1.0）；负样本与查询余弦 ~0.99，
        # 默认裕度 0.02 下 0.99 >= 0.98 拦截；裕度 0 时 0.99 < 1.0 放行。
        mapping = {
            "删掉订单表": [0.0, 0.0, 0.0, 1.0],
            "帮我写一个删除订单表的脚本": [0.0, 0.0, 0.14, 0.99],
            "删掉订单表吧": [0.0, 0.0, 0.0, 1.0],
        }
        out = await _router(routes, mapping).route("删掉订单表吧")
        assert out is not None
        assert out["_route"] == "db_drop"


# ---- BM25 混合检索 --------------------------------------------------------------


class TestHybridBm25:
    def test_tokenize_ascii_and_bigram(self):
        toks = _tokenize("查询 order_main 表 10.0.0.5")
        assert "order_main" in toks
        assert "10.0.0.5" in toks
        assert "查询" in toks  # 中文 bigram

    def test_bm25_scores_favor_exact_match(self):
        routes = (
            Route(name="a", utterances=("查询订单表",)),
            Route(name="b", utterances=("查询用户表",)),
        )
        scores = _bm25_route_scores(_tokenize("查询用户表"), routes)
        assert scores["b"] > scores["a"]

    async def test_hybrid_breaks_vector_tie(self, monkeypatch):
        """向量平手时，BM25 关键词信号决定赢家。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "semantic_route_hybrid_weight", 0.35)
        routes = (
            Route(name="order", utterances=("查询订单表",), analysis={"intent": "query"}),
            Route(name="user", utterances=("查询用户表",), analysis={"intent": "query"}),
        )
        # 两路由正样本同向量（极端平手）；查询与「用户表」关键词完全一致
        mapping = {
            "查询订单表": [0.0, 0.0, 1.0, 0.0],
            "查询用户表": [0.0, 0.0, 1.0, 0.0],
        }
        out = await _router(routes, mapping).route("查询用户表")
        assert out is not None
        assert out["_route"] == "user"

    async def test_hybrid_weight_zero_pure_vector(self, monkeypatch):
        """权重 0 → 纯向量行为（平手取首个最高分路由）。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "semantic_route_hybrid_weight", 0.0)
        routes = (
            Route(name="order", utterances=("查询订单表",), analysis={"intent": "query"}),
            Route(name="user", utterances=("查询用户表",), analysis={"intent": "query"}),
        )
        mapping = {
            "查询订单表": [0.0, 0.0, 1.0, 0.0],
            "查询用户表": [0.0, 0.0, 1.0, 0.0],
        }
        out = await _router(routes, mapping).route("查询用户表")
        assert out is not None  # 平手命中其一即可（不断言具体路由）


# ---- 风险分级阈值 ----------------------------------------------------------------

_RISK_ROUTES = (
    Route(
        name="db_drop",
        utterances=("删掉订单表",),
        analysis={"intent": "mutate", "risk_level": "critical", "confidence": 0.9},
        high_risk=True,
    ),
)
# 查询向量与示例句余弦 0.85
_RISK_MAPPING = {
    "删掉订单表": [1.0, 0.0],
    "删掉订单表吧": [0.85, 0.5268],
}


class TestRiskThresholds:
    async def test_high_risk_threshold_blocks_medium_score(self, monkeypatch):
        """高风险阈值抬高后，中等相似度未命中（向量 0.85，融合后 ~0.90 < 0.95）。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "semantic_route_high_risk_threshold", 0.95)
        out = await _router(_RISK_ROUTES, _RISK_MAPPING).route("删掉订单表吧")
        assert out is None

    async def test_high_risk_threshold_passes_when_loosened(self, monkeypatch):
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "semantic_route_high_risk_threshold", 0.8)
        out = await _router(_RISK_ROUTES, _RISK_MAPPING).route("删掉订单表吧")
        assert out is not None
        assert out["_route"] == "db_drop"

    async def test_route_level_threshold_override(self, monkeypatch):
        """路由自带 threshold 优先于全局默认。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        routes = (
            Route(
                name="strict",
                utterances=("暗号芝麻开门",),
                analysis={"intent": "query"},
                threshold=0.99,
            ),
        )
        mapping = {"暗号芝麻开门": [1.0, 0.0], "暗号芝麻开门呀": [0.95, 0.3122]}
        out = await _router(routes, mapping).route("暗号芝麻开门呀")
        assert out is None  # 0.95 < 0.99

    async def test_high_risk_hit_forces_clarification(self, monkeypatch):
        """高风险命中：强制 need_clarification=True + 置信度下调 + 追问话术。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "semantic_route_high_risk_threshold", 0.8)
        out = await _router(_RISK_ROUTES, _RISK_MAPPING).route("删掉订单表吧")
        assert out is not None
        assert out["need_clarification"] is True
        assert out["confidence"] <= 0.75
        assert out["clarification_message"]


# ---- 动态负样本（闭环反馈库）------------------------------------------------------


class TestDynamicNegatives:
    async def test_feedback_hard_sample_blocks_route(self, monkeypatch):
        """闭环反馈困难样本与查询同向量 → 全局拦截。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        routes = (Route(name="db_query", utterances=("查询订单表",), analysis={"intent": "query"}),)
        hard_text = "查询订单表但上次执行错了"
        mapping = {
            "查询订单表": [0.0, 0.0, 1.0, 0.0],
            hard_text: [0.0, 0.0, 1.0, 0.0],  # 与查询完全同向
        }

        async def _fake_load(limit: int = 50):
            return [hard_text]

        monkeypatch.setattr(sr, "_load_hard_samples", _fake_load)
        out = await _router(routes, mapping).route(hard_text)
        assert out is None

    async def test_load_failure_silently_empty(self, monkeypatch):
        """困难样本读取异常 → 空列表，不影响路由。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)

        async def _broken(limit: int = 50):
            raise RuntimeError("db down")

        monkeypatch.setattr(sr, "_load_hard_samples", _broken)
        routes = (Route(name="db_query", utterances=("查询订单表",), analysis={"intent": "query"}),)
        mapping = {"查询订单表": [0.0, 0.0, 1.0, 0.0]}
        out = await _router(routes, mapping).route("查询订单表")
        assert out is not None
        assert out["_route"] == "db_query"
