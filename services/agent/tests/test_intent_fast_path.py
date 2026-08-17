"""明确操作免规划 —— 意图分析置信度契约 + decompose 快速路径回归（2026-08-17）。

背景：用户 ping 内网模型这类明确操作请求，因两层叠加缺陷仍走 30s+ 的
编排决策器 LLM：
    1. `IntentAnalysis.from_plain_intent` 固定 confidence=0.5，低于快速路径
       门槛（>= 0.6）—— 本地模型缺席降级到 plain 兜底时全部请求掉进 LLM 决策；
    2. `LMRouter.analyze_intent` 返回 IntentAnalysis 对象，而调用方 intent_node
       用 isinstance(analysis, dict) 判定 → 分析结果被静默丢弃，
       state.intent_analysis 永远缺失，快速路径从未生效。

本文件锁定修复后的契约：
    - 明确操作型意图（query / mutate / orchestrate）兜底置信度 0.6（直达快路径）；
      闲聊保持 0.5
    - analyze_intent 返回 dict（与 semantic_route 契约一致）
    - intent_node 把 dict 分析写入 state（structured=True）
    - decompose 快速路径：0.6 + need_tool → TOOL_ONLY 且零 LLM；
      0.5 低置信度 → 仍交编排决策器 LLM 复核
"""

from __future__ import annotations

import pytest
from agent.graph.nodes.decompose import decompose_node
from agent.graph.nodes.intent import intent_node
from agent.graph.state import empty_state
from agent.llm.router import LMRouter
from agent.llm.types import IntentAnalysis

# ---- from_plain_intent 置信度策略 ------------------------------------------


class TestPlainIntentConfidence:
    @pytest.mark.parametrize("intent", ["query", "mutate", "orchestrate"])
    def test_operational_intent_gets_fast_path_confidence(self, intent):
        """明确操作型意图给 0.6 —— 刚好过 decompose 快速路径门槛。"""
        a = IntentAnalysis.from_plain_intent(intent, "帮我ping一下内网模型")
        assert a.confidence == pytest.approx(0.6)
        assert a.need_tool is True

    def test_chitchat_stays_below_threshold(self):
        """闲聊保持 0.5（decompose 节点对闲聊本就前置跳过，无需快路径）。"""
        a = IntentAnalysis.from_plain_intent("chitchat", "你好")
        assert a.confidence == pytest.approx(0.5)
        assert a.need_tool is False

    def test_to_dict_carries_confidence(self):
        """降级链经 to_dict → from_raw 往返后置信度不丢。"""
        d = IntentAnalysis.from_plain_intent("query", "测连通性", backend="plain").to_dict()
        roundtrip = IntentAnalysis.from_raw(d, fallback_text="测连通性", backend="plain")
        assert roundtrip.confidence == pytest.approx(0.6)
        assert roundtrip.need_tool is True


# ---- analyze_intent 返回契约 ------------------------------------------------


class TestAnalyzeIntentContract:
    async def test_returns_dict_via_plain_fallback(self):
        """plain 兜底必须返回 dict —— 调用方 intent_node 靠 isinstance(dict) 判定。"""
        router = LMRouter.__new__(LMRouter)  # 绕过 __init__ 的重依赖
        router._mock_mode = True  # 只挂 plain 一级

        async def _classify(text):
            return "query"

        router.classify_intent = _classify  # 实例属性遮蔽方法

        out = await router.analyze_intent("帮我ping一下内网模型，看看通不通")
        assert isinstance(out, dict)
        assert out["intent"] == "query"
        assert out["need_tool"] is True
        assert out["confidence"] == pytest.approx(0.6)
        assert out["backend"] == "plain"

    async def test_empty_text_returns_dict(self):
        router = LMRouter.__new__(LMRouter)
        router._mock_mode = True
        out = await router.analyze_intent("")
        assert isinstance(out, dict)
        assert out["intent"] == "chitchat"


# ---- intent_node 写入 state --------------------------------------------------


class _DictAnalysisLLM:
    """analyze_intent 返回 dict 的替身（修复后的真实契约）。"""

    def __init__(self, analysis: dict):
        self._analysis = analysis
        self.classify_calls = 0

    async def analyze_intent(self, text, history=None, page_context=""):
        return self._analysis

    async def classify_intent(self, text):
        self.classify_calls += 1
        return "query"


class TestIntentNodeStructured:
    async def test_dict_analysis_stored_in_state(self):
        """dict 分析结果写入 intent_analysis（structured=True），不再静默丢弃。"""
        analysis = IntentAnalysis.from_plain_intent(
            "query", "帮我ping一下内网模型", backend="plain"
        ).to_dict()
        llm = _DictAnalysisLLM(analysis)
        st = empty_state("帮我ping一下内网模型")
        st["run_id"] = "run-fast-path"

        out = await intent_node(st, llm)

        assert out["intent"] == "query"
        assert isinstance(out.get("intent_analysis"), dict)
        assert out["intent_analysis"]["confidence"] == pytest.approx(0.6)
        assert llm.classify_calls == 0  # 结构化路径不再回退旧式分类
        trace = out["trace"][0]
        assert trace.get("structured") is True

    async def test_non_dict_analysis_falls_back_to_classify(self):
        """旧式对象返回（注入替身）仍安全回退 classify_intent。"""

        class _ObjectAnalysisLLM(_DictAnalysisLLM):
            async def analyze_intent(self, text, history=None, page_context=""):
                return object()  # 非 dict → 走旧式回退

        llm = _ObjectAnalysisLLM({})
        st = empty_state("帮我ping一下内网模型")
        st["run_id"] = "run-fallback"

        out = await intent_node(st, llm)

        assert out["intent"] == "query"
        assert "intent_analysis" not in out
        assert llm.classify_calls == 1


# ---- decompose 快速路径 ------------------------------------------------------


class _DeciderLLM:
    """记录 decompose 调用的替身（快路径生效时应为零调用）。"""

    def __init__(self):
        self.decompose_calls = 0

    async def decompose(self, **kwargs):
        self.decompose_calls += 1
        return None  # 返回 None → 走 TOOL_ONLY 降级（若真被调到）


def _state_with_analysis(analysis: dict) -> dict:
    st = empty_state("帮我ping一下内网模型")
    st["run_id"] = "run-fast-path"
    st["intent"] = analysis.get("intent") or "query"
    st["intent_analysis"] = analysis
    return st


class TestDecomposeFastPath:
    async def test_plain_query_direct_to_tool_loop_without_llm(self, monkeypatch):
        """0.6 + need_tool → TOOL_ONLY 直达工具循环，不调编排决策器 LLM。"""
        from agent.config import settings

        monkeypatch.setattr(settings, "tool_loop_enabled", True)
        analysis = IntentAnalysis.from_plain_intent(
            "query", "帮我ping一下内网模型", backend="plain"
        ).to_dict()
        llm = _DeciderLLM()

        out = await decompose_node(_state_with_analysis(analysis), llm)

        inner = out["decompose_decision"]["decision"]
        assert inner["mode"] == "TOOL_ONLY"
        assert inner["reason"] == "意图分析判定需要工具 → 动态工具循环"
        assert out["trace"][0].get("reason") == "intent_analysis_need_tool"
        assert llm.decompose_calls == 0  # 明确操作不需要规划

    async def test_low_confidence_still_reviews_via_llm(self, monkeypatch):
        """低于 0.6 的分析仍交编排决策器 LLM 复核（保守门槛不变）。"""
        from agent.config import settings

        monkeypatch.setattr(settings, "tool_loop_enabled", True)
        analysis = IntentAnalysis.from_plain_intent(
            "query", "帮我ping一下内网模型", backend="plain"
        ).to_dict()
        analysis["confidence"] = 0.5
        llm = _DeciderLLM()

        await decompose_node(_state_with_analysis(analysis), llm)

        assert llm.decompose_calls == 1

    async def test_no_need_tool_direct_answer(self, monkeypatch):
        """need_tool=False → MAIN_AGENT 直接回答，同样零 LLM 决策。"""
        from agent.config import settings

        monkeypatch.setattr(settings, "tool_loop_enabled", True)
        analysis = {
            "intent": "query",
            "intent_category": "knowledge_qa",
            "confidence": 0.9,
            "need_tool": False,
            "need_clarification": False,
        }
        llm = _DeciderLLM()

        out = await decompose_node(_state_with_analysis(analysis), llm)

        assert out["decompose_decision"]["decision"]["mode"] == "MAIN_AGENT"
        assert llm.decompose_calls == 0
