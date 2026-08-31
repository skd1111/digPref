"""Skill 强钉（`/` 指令）—— 短路自动路由，低优先级命中物理排除（2026-08-28）。

背景：`/` 手动选、功能点绑定、关键词/LLM 自动路由三条 skill 注入路径此前无互斥，
多段「必须严格执行以下规范」叠加时本地小模型规则互掐、云端大模型浪费 token。
强钉策略（优先级：手动钉 > 绑定 > 自动路由）：排除发生在代码组装阶段——
intent_node 见到 pinned_skill_id 直接跳过 SkillRouter（连 LLM 分类调用都省了），
固定 active_skill_id；不依赖「最高优先级指令」这类软覆盖（小模型指令遵循不可靠）。
钉住目标不存在/已停用 → 回退正常路由，绝不阻断对话。
"""

from __future__ import annotations

import pytest
from agent.graph.nodes.intent import _load_pinned_skill, intent_node
from agent.graph.state import empty_state
from agent.llm.types import IntentAnalysis
from agent.skills import api as skills_api
from agent.skills.models import Skill, SkillRoutingResult


class _FakeLoader:
    def __init__(self, skills: dict[str, Skill]):
        self._skills = skills

    def get(self, skill_id: str):
        return self._skills.get(skill_id)


def _skill(sid: str, name: str, enabled: bool = True) -> Skill:
    return Skill(id=sid, name=name, enabled=enabled)


class _DictAnalysisLLM:
    """analyze_intent 返回 dict 的替身（同 test_intent_fast_path 契约）。"""

    def __init__(self, analysis: dict):
        self._analysis = analysis

    async def analyze_intent(self, text, history=None, page_context=""):
        return self._analysis

    async def classify_intent(self, text):
        return "query"


def _analysis_dict() -> dict:
    return IntentAnalysis.from_plain_intent("query", "做个ppt", backend="plain").to_dict()


# ---- _load_pinned_skill 装载契约 --------------------------------------------


class TestLoadPinnedSkill:
    def test_valid_skill_returns_confidence_one(self, monkeypatch):
        monkeypatch.setattr(skills_api, "_loader", _FakeLoader({"s1": _skill("s1", "PPT 规范")}))
        r = _load_pinned_skill("s1")
        assert r is not None
        assert r.skill_id == "s1"
        assert r.skill_name == "PPT 规范"
        assert r.confidence == pytest.approx(1.0)

    def test_disabled_or_missing_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            skills_api, "_loader", _FakeLoader({"s2": _skill("s2", "停用技能", enabled=False)})
        )
        assert _load_pinned_skill("s2") is None  # 已停用 → 回退正常路由
        assert _load_pinned_skill("ghost") is None  # 不存在 → 回退正常路由

    def test_blank_returns_none(self):
        assert _load_pinned_skill("") is None
        assert _load_pinned_skill("   ") is None


# ---- intent_node 强钉短路 ----------------------------------------------------


class TestIntentNodePinnedShortCircuit:
    async def test_pinned_skips_router_entirely(self, monkeypatch):
        """强钉生效时 SkillRouter 连构造都不允许（关键词/LLM 分类全跳过）。"""
        import agent.skills.router as skills_router

        class _Boom:
            def __init__(self, *a, **k):
                raise AssertionError("强钉路径不得构造 SkillRouter")

        monkeypatch.setattr(skills_router, "SkillRouter", _Boom)
        monkeypatch.setattr(skills_api, "_loader", _FakeLoader({"s1": _skill("s1", "PPT 规范")}))

        st = empty_state("做一个介绍你自己的ppt")
        st["run_id"] = "run-pinned"
        st["pinned_skill_id"] = "s1"

        out = await intent_node(st, _DictAnalysisLLM(_analysis_dict()))

        assert out["active_skill_id"] == "s1"
        assert out["active_skill_name"] == "PPT 规范"

    async def test_invalid_pin_falls_back_to_normal_routing(self, monkeypatch):
        """钉住的目标已停用/被删 → 不阻断，回退正常路由链。"""
        import agent.skills.router as skills_router
        from agent.llm import router as llm_router

        monkeypatch.setattr(
            skills_api, "_loader", _FakeLoader({"s2": _skill("s2", "停用技能", enabled=False)})
        )
        monkeypatch.setattr(
            llm_router, "load_enabled_local_backend", lambda: ("http://127.0.0.1:11434",)
        )

        class _StubRouter:
            calls = 0

            def __init__(self, *a, **k):
                type(self).calls += 1

            async def route_async(self, prompt):
                return SkillRoutingResult(skill_id="s3", skill_name="关键词命中", confidence=0.9)

        monkeypatch.setattr(skills_router, "SkillRouter", _StubRouter)

        st = empty_state("做一个介绍你自己的ppt")
        st["run_id"] = "run-pin-invalid"
        st["pinned_skill_id"] = "s2"  # 已停用 → 强钉失效

        out = await intent_node(st, _DictAnalysisLLM(_analysis_dict()))

        assert _StubRouter.calls == 1  # 回退走了正常路由
        assert out["active_skill_id"] == "s3"
