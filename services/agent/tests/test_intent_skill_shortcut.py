"""intent 节点 Skill 路由快路径短路（2026-09-01 首响性能修复）回归。

背景：语义路由直出（_route 命中）与闲聊本就不需要 Skill 规范注入，但此前仍会
走 SkillRouter 探活 + 最多两轮 Ollama 分类（最坏 ~11s），且该阶段完全无 trace。
修复：两类快路径直接短路（强钉是用户显式动作，仍保持最高优先级单独处理）；
同时 skill_route 阶段单独落 trace 条目，隐形耗时显形。
"""

from __future__ import annotations

import pytest
from agent.graph.nodes.intent import intent_node
from agent.graph.state import empty_state
from agent.llm.types import IntentAnalysis
from agent.skills import api as skills_api
from agent.skills.models import Skill, SkillRoutingResult


class _FakeLoader:
    def __init__(self, skills: dict[str, Skill]):
        self._skills = skills

    def get(self, skill_id: str):
        return self._skills.get(skill_id)

    def list(self):
        return list(self._skills.values())


def _skill(sid: str, name: str, enabled: bool = True) -> Skill:
    return Skill(id=sid, name=name, enabled=enabled)


class _DictAnalysisLLM:
    """analyze_intent 返回 dict 的替身（同 test_intent_pinned_skill 契约）。"""

    def __init__(self, analysis: dict):
        self._analysis = analysis

    async def analyze_intent(self, text, history=None, page_context=""):
        return self._analysis

    async def classify_intent(self, text):
        return "query"


def _chitchat_analysis() -> dict:
    return IntentAnalysis.from_plain_intent("chitchat", "你好", backend="plain").to_dict()


def _route_analysis() -> dict:
    """语义路由直出的分析形状（含 _route 来源标记）。"""
    a = IntentAnalysis.from_plain_intent("query", "查询订单", backend="semantic_route").to_dict()
    a["_route"] = "db_query"
    return a


def _skill_route_entry(out: dict) -> dict:
    entries = [t for t in out["trace"] if t.get("node") == "skill_route"]
    assert len(entries) == 1, f"skill_route trace 应恰有一条：{out['trace']}"
    return entries[0]


@pytest.fixture
def boom_router(monkeypatch):
    """SkillRouter 一旦被构造即失败 —— 断言快路径短路彻底。"""
    import agent.skills.router as skills_router

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("快路径短路不得构造 SkillRouter")

    monkeypatch.setattr(skills_router, "SkillRouter", _Boom)
    monkeypatch.setattr(skills_api, "_loader", _FakeLoader({"s1": _skill("s1", "PPT 规范")}))


class TestFastPathShortCircuit:
    async def test_chitchat_skips_router(self, boom_router):
        """闲聊 → SkillRouter 连构造都不允许，且无 active_skill 输出。"""
        st = empty_state("你好")
        st["run_id"] = "run-sr-chitchat"

        out = await intent_node(st, _DictAnalysisLLM(_chitchat_analysis()))

        assert out["intent"] == "chitchat"
        assert "active_skill_id" not in out
        entry = _skill_route_entry(out)
        assert entry["status"] == "skipped"
        assert entry["mode"] == "fast_path"
        assert entry["reason"] == "chitchat"
        assert entry["duration_ms"] >= 0

    async def test_semantic_route_hit_skips_router(self, boom_router):
        """语义路由直出（_route 标记）→ 即使是 query 意图也短路。"""
        st = empty_state("查询订单")
        st["run_id"] = "run-sr-hit"

        out = await intent_node(st, _DictAnalysisLLM(_route_analysis()))

        assert out["intent"] == "query"
        assert "active_skill_id" not in out
        entry = _skill_route_entry(out)
        assert entry["status"] == "skipped"
        assert entry["reason"] == "semantic_route_hit"

    async def test_plain_query_still_routes(self, monkeypatch):
        """对照组：普通 query（无 _route）仍走正常路由并落 ok 条目。"""
        import agent.skills.router as skills_router
        from agent.llm import router as llm_router

        monkeypatch.setattr(skills_api, "_loader", _FakeLoader({"s1": _skill("s1", "PPT 规范")}))
        monkeypatch.setattr(
            llm_router, "load_enabled_local_backend", lambda: ("http://127.0.0.1:11434",)
        )

        class _StubRouter:
            calls = 0

            def __init__(self, *a, **k):
                type(self).calls += 1

            async def route_async(self, prompt):
                return SkillRoutingResult(skill_id="s1", skill_name="PPT 规范", confidence=0.9)

        monkeypatch.setattr(skills_router, "SkillRouter", _StubRouter)

        analysis = IntentAnalysis.from_plain_intent("query", "做个ppt", backend="plain").to_dict()
        st = empty_state("做一个介绍你自己的ppt")
        st["run_id"] = "run-normal-route"

        out = await intent_node(st, _DictAnalysisLLM(analysis))

        assert _StubRouter.calls == 1
        assert out["active_skill_id"] == "s1"
        entry = _skill_route_entry(out)
        assert entry["status"] == "ok"
        assert entry["mode"] == "routed"
