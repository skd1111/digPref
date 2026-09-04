"""语义路由「知识库检索/确认追问」路由测试（Fix 2，根因修复 2026-09-04）。

缺口：用户对上一轮澄清回复「知识库里没有吗」这类短句，旧 DEFAULT_ROUTES 未覆盖，
语义向量分数低于阈值未命中 → 回退云端 LLM，被强行改写成 data_query + need_tool=true
→ decompose 判 TOOL_ONLY → 跑去 shell 全盘翻找卡死。新增 kb_lookup 路由零 LLM
直出 need_tool=False，走 RAG 召回直接作答，不动用文件工具。
"""

from __future__ import annotations

from agent.config import settings
from agent.graph.semantic_route import DEFAULT_ROUTES, SemanticIntentRouter


class _KbFakeEmbedding:
    """6 维玩具空间：知识库追问 → e1；负样本(脚本/接口/清空/删除) → e5（与 e1 正交）；
    其余高频路由各占一轴；未知 → e6。负样本词优先判定，避免与正样本共轴触发拦截。
    """

    model = "fake-kb"

    def __init__(self) -> None:
        self.healthy = True

    def _vec(self, text: str) -> list[float]:
        if any(k in text for k in ("脚本", "接口", "优化", "清空", "删除", "删掉")):
            return [0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        if any(k in text for k in ("知识库", "库里", "内部资料", "内部文档", "文档里")):
            return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if any(k in text for k in ("你好", "您好", "嗨", "在吗", "你是谁", "谢谢", "再见")):
            return [0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
        if any(k in text for k in ("几号", "几点", "农历", "星期", "日期", "时间")):
            return [0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
        if any(k in text for k in ("查询", "查一下", "订单表", "用户表")):
            return [0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    async def health_check(self) -> bool:
        return self.healthy

    async def embed(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


def _router() -> SemanticIntentRouter:
    return SemanticIntentRouter(DEFAULT_ROUTES, embedding=_KbFakeEmbedding())


def test_kb_lookup_route_defined():
    route = next((r for r in DEFAULT_ROUTES if r.name == "kb_lookup"), None)
    assert route is not None
    assert route.analysis["need_tool"] is False
    assert route.analysis["intent"] == "query"
    assert "知识库里没有吗" in route.utterances


class TestKbLookupRoute:
    async def test_hit_kb_confirm_followup(self, monkeypatch):
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        out = await _router().route("知识库里没有吗")
        assert out is not None
        assert out["_route"] == "kb_lookup"
        assert out["intent"] == "query"
        # 关键：不动用工具 → decompose 会走 MAIN_AGENT 据 RAG 作答
        assert out["need_tool"] is False

    async def test_hard_negative_script_blocked(self, monkeypatch):
        """「写个查知识库的脚本」是开发任务，不应命中 kb_lookup。"""
        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        out = await _router().route("帮我写一个查询知识库的脚本")
        assert out is None or out.get("_route") != "kb_lookup"

    async def test_intent_node_kb_lookup_skips_llm(self, monkeypatch):
        from agent.graph import semantic_route as sr
        from agent.graph.nodes.intent import intent_node
        from agent.graph.state import empty_state

        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        monkeypatch.setattr(settings, "local_embedding_base_url", "http://fake:1/v1")
        monkeypatch.setattr(sr, "_default_router", _router())

        class _ExplodingLLM:
            async def analyze_intent(self, text, history=None, **kw):
                raise AssertionError("向量命中后不应调 LLM 改写意图")

            async def classify_intent(self, text):
                raise AssertionError("向量命中后不应调 LLM")

        st = empty_state("知识库里没有吗")
        st["run_id"] = "run-kb"
        out = await intent_node(st, _ExplodingLLM())
        assert out["intent"] == "query"
        assert out["intent_analysis"]["_route"] == "kb_lookup"
        assert out["intent_analysis"]["need_tool"] is False
        monkeypatch.setattr(sr, "_default_router", None)
