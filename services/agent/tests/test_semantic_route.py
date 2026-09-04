"""意图向量快速路由（semantic-router 模式）测试。

覆盖：
    - 命中/未命中路由（余弦阈值）
    - embedding 不可用 / 全零向量 → 静默回退（返 None）
    - 开关关闭 → 不调 embedding
    - 向量缓存落 sqlite-vec（semantic_route.db）+ 指纹失效重建
    - intent_node / responder 集成（闲聊模板直回）
"""

from __future__ import annotations

import sqlite3

import pytest
from agent import vector_store as vs
from agent.config import settings
from agent.graph.semantic_route import (
    DEFAULT_ROUTES,
    Route,
    SemanticIntentRouter,
    _cosine,
)

# ---- 确定性伪 embedding 客户端 ----------------------------------------------


class _FakeEmbedding:
    """5 维玩具向量空间：闲聊 → e1，时间 → e2，DB 查询 → e3，删除/远程 → e4，
    未知 → e5（与各路由轴正交，余弦 0 → 必未命中，且不触发负样本拦截）。
    V2（2026-08-31）：新增 db_query / db_drop / ssh_execute 预置路由后相应扩维。

    embed_calls 计数用于验证缓存命中后不重复批量向量化。
    """

    model = "fake-4d"

    def __init__(self) -> None:
        self.embed_calls = 0
        self.batch_calls = 0
        self.healthy = True

    def _vec(self, text: str) -> list[float]:
        if any(
            k in text
            for k in (
                "你好",
                "您好",
                "嗨",
                "在吗",
                "你是谁",
                "介绍",
                "谢谢",
                "多谢",
                "辛苦",
                "再见",
                "拜",
            )
        ):
            return [1.0, 0.0, 0.0, 0.0, 0.0]
        if any(k in text for k in ("几号", "几点", "农历", "星期", "日期", "时间")):
            return [0.0, 1.0, 0.0, 0.0, 0.0]
        if any(k in text for k in ("删", "清空", "连上", "连接", "登录", "SSH", "重启", "服务器")):
            return [0.0, 0.0, 0.0, 1.0, 0.0]
        if any(k in text for k in ("查询", "查一下", "订单表", "用户表")):
            return [0.0, 0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 0.0, 0.0, 1.0]  # 未知文本 → 独占轴，与所有路由向量余弦 0

    async def health_check(self) -> bool:
        return self.healthy

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return self._vec(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [self._vec(t) for t in texts]


def _router(fake: _FakeEmbedding, **kw) -> SemanticIntentRouter:
    return SemanticIntentRouter(DEFAULT_ROUTES, embedding=fake, **kw)


# ---- 基础 -------------------------------------------------------------------


def test_cosine():
    assert _cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert _cosine([], []) == 0.0
    assert _cosine([0, 0], [1, 1]) == 0.0


# ---- 路由命中 / 未命中 -------------------------------------------------------


class TestSemanticRoute:
    async def test_hit_chitchat(self, monkeypatch):
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        fake = _FakeEmbedding()
        out = await _router(fake).route("你好呀")
        assert out is not None
        assert out["intent"] == "chitchat"
        assert out["_route"] == "chitchat"
        assert out["canned_response"]
        assert out["rewritten_query"] == "你好呀"

    async def test_hit_time_query(self, monkeypatch):
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        fake = _FakeEmbedding()
        out = await _router(fake).route("请问今天几号")
        assert out is not None
        assert out["_route"] == "time_query"
        assert out["need_tool"] is True

    async def test_miss_returns_none(self, monkeypatch):
        """未知向量轴命中不了任何路由；即使关键词与负样本重叠也被拦截。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        fake = _FakeEmbedding()
        out = await _router(fake).route("帮我分析这份合同的法律风险并出具审查意见")
        assert out is None
        # 含 DB 关键词但更像「编写脚本」负样本 → 负样本拦截回退（BM25 也抬不起来）
        fake2 = _FakeEmbedding()
        out2 = await _router(fake2).route("写个查询库存的脚本先别执行")
        assert out2 is None

    async def test_disabled_skips_embedding(self, monkeypatch):
        monkeypatch.setattr(settings, "semantic_route_enabled", False)
        fake = _FakeEmbedding()
        out = await _router(fake).route("你好")
        assert out is None
        assert fake.embed_calls == 0 and fake.batch_calls == 0

    async def test_unhealthy_embedding_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        fake = _FakeEmbedding()
        fake.healthy = False
        out = await _router(fake).route("你好")
        assert out is None
        # 探活失败后不再重试（会话内标记不可用）
        out2 = await _router(fake).route("你好")
        assert out2 is None

    async def test_threshold_guard(self, monkeypatch):
        """阈值调高到 1.0 后，非完全匹配一律未命中。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        fake = _FakeEmbedding()
        out = await _router(fake, threshold=1.0).route("你好呀")
        # 「你好呀」向量 (1,0) 与示例句 (1,0) 完全一致 → 仍命中
        assert out is not None
        fake2 = _FakeEmbedding()
        out2 = await _router(fake2, threshold=1.0).route("麻烦帮个忙")
        assert out2 is None


# ---- 缓存 --------------------------------------------------------------------


class TestSemanticRouteCache:
    async def test_cache_written_and_reused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "knowledge_db_path", str(tmp_path / "knowledge.db"))
        fake = _FakeEmbedding()
        await _router(fake).route("你好")
        db = tmp_path / "semantic_route.db"
        assert db.exists()
        # vec0 虚拟表里的正样本向量数 = 全部预置路由示例句数；指纹已落库
        conn = sqlite3.connect(str(db))
        try:
            assert vs.load_extension(conn)
            fp = conn.execute("SELECT fingerprint FROM route_meta WHERE id = 1").fetchone()[0]
            assert fp
            pos_count = conn.execute(
                "SELECT COUNT(*) FROM route_vec_ref WHERE kind = 'pos'"
            ).fetchone()[0]
            assert pos_count == sum(len(r.utterances) for r in DEFAULT_ROUTES)
        finally:
            conn.close()

        # 新实例复用缓存 → 不再批量向量化，只产生 1 次查询 embed
        fake2 = _FakeEmbedding()
        out = await _router(fake2).route("你好")
        assert out is not None
        assert fake2.batch_calls == 0
        assert fake2.embed_calls == 1

    async def test_cache_invalidated_by_routes_change(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "knowledge_db_path", str(tmp_path / "knowledge.db"))
        fake = _FakeEmbedding()
        await _router(fake).route("你好")

        extra = (*DEFAULT_ROUTES, Route(name="custom", utterances=("暗号",)))
        fake2 = _FakeEmbedding()
        await SemanticIntentRouter(extra, embedding=fake2).route("你好")
        assert fake2.batch_calls == 1  # 指纹变化 → 缓存失效重算


# ---- 集成：intent_node + responder -------------------------------------------


class TestSemanticRouteIntegration:
    async def test_intent_node_vector_path_skips_llm(self, monkeypatch):
        from agent.graph import semantic_route as sr
        from agent.graph.nodes.intent import intent_node
        from agent.graph.state import empty_state

        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "local_embedding_base_url", "http://fake:1/v1")
        monkeypatch.setattr(
            sr,
            "_default_router",
            SemanticIntentRouter(
                DEFAULT_ROUTES,
                embedding=_FakeEmbedding(),
            ),
        )

        class _ExplodingLLM:
            async def analyze_intent(self, text, history=None):
                raise AssertionError("向量命中后不应调 LLM")

            async def classify_intent(self, text):
                raise AssertionError("向量命中后不应调 LLM")

        st = empty_state("你好呀")
        st["run_id"] = "run-sr"
        out = await intent_node(st, _ExplodingLLM())
        assert out["intent"] == "chitchat"
        assert out["intent_analysis"]["_route"] == "chitchat"
        monkeypatch.setattr(sr, "_default_router", None)

    async def test_responder_canned_response_no_llm(self, monkeypatch):
        from agent.graph.nodes.responder import responder_node
        from agent.graph.state import empty_state

        st = empty_state("你好呀")
        st["intent"] = "chitchat"
        st["intent_analysis"] = {
            "intent": "chitchat",
            "need_tool": False,
            "confidence": 0.9,
            "canned_response": "你好，我是 EAIDE 企业 AI 助理。告诉我你想查询或操作哪个系统吧。",
        }
        st["decompose_decision"] = {
            "decision": {"mode": "MAIN_AGENT", "clarifying_questions": []},
        }

        class _ExplodingLLM:
            async def summarise(self, **kw):
                raise AssertionError("canned_response 应直回，不调 summarise")

        out = await responder_node(st, _ExplodingLLM())
        assert "EAIDE 企业 AI 助理" in out["final_answer"]
