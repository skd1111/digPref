"""BUGFIX #155 —— 命中的 skill 规范必须注入工具循环（2026-08-26）。

真实翻车（日志 288ce22e）：「做一个介绍你自己的ppt」intent 命中
office_pptx_designer，但 skill.system_prompt 从未进入执行循环（build_system_prompt
零调用方）→ 模型脱离设计规范裸追问「您要包含个人信息/教育背景吗？」（主语还错乱）。
修复：_active_skill_addon 拼规范段，native/提示词协议两条循环路径注入。
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from agent.skills import api as skills_api
from agent.tools.loop import DynamicToolLoop, _active_skill_addon, _merge_extra_rules


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeSkill:
    name = "PPT 汇报生成规范"
    system_prompt = "先声明设计约束再生成；固定五步工具编排。"
    few_shot_examples: ClassVar[list] = []


class _FakeLoader:
    def get(self, skill_id):
        if skill_id == "office_pptx_designer":
            return _FakeSkill()
        return None


class _StubBackend:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.request_messages: list[list[dict]] = []

    async def chat_with_tools(self, messages, tools, **kwargs):
        self.request_messages.append([dict(m) for m in messages])
        return self._scripted.pop(0)


class _StubLLM:
    def __init__(self, backend):
        self._backend = backend
        self.orchestrate_kwargs: list[dict] = []

    async def resolve_native_backend(self):
        return ("cloud", self._backend)

    async def orchestrate_tools(self, **kwargs):
        self.orchestrate_kwargs.append(dict(kwargs))
        return {"action": "FINAL_ANSWER", "final_answer": "done"}


class _StubCatalog:
    async def definitions(self, names=None):
        return []

    async def summaries(self):
        return []


def test_active_skill_addon_builds_norm_block(monkeypatch):
    monkeypatch.setattr(skills_api, "_loader", _FakeLoader())
    addon = _active_skill_addon({"active_skill_id": "office_pptx_designer"})
    assert "已绑定 Skill：PPT 汇报生成规范" in addon
    assert "先声明设计约束再生成" in addon
    # 主语纪律：追问必须与用户请求主语一致（防「介绍你自己」漂成用户简历）
    assert "禁止偷换成用户本人" in addon


def test_active_skill_addon_empty_without_hit(monkeypatch):
    monkeypatch.setattr(skills_api, "_loader", _FakeLoader())
    assert _active_skill_addon({}) == ""
    assert _active_skill_addon({"active_skill_id": "no_such_skill"}) == ""


def test_native_loop_system_prompt_includes_skill(monkeypatch):
    """native 循环首轮 system 消息携带 skill 规范。"""
    monkeypatch.setattr(skills_api, "_loader", _FakeLoader())
    backend = _StubBackend([{"content": "完成。", "tool_calls": []}])
    loop = DynamicToolLoop(_StubLLM(backend), _StubCatalog())
    state = {
        "tool_calling_mode": "native",
        "user_prompt": "做一个介绍你自己的ppt",
        "messages": [],
        "tool_results": [],
        "tool_turn_count": 0,
        "active_skill_id": "office_pptx_designer",
    }
    out = _run(loop.run(state))
    assert out["final_answer"] == "完成。"
    system = backend.request_messages[0][0]
    assert system["role"] == "system"
    assert "已绑定 Skill：PPT 汇报生成规范" in system["content"]


def test_prompt_mode_loop_passes_skill_via_extra_rules(monkeypatch):
    """提示词协议循环：skill 规范走 4.13 EXTRA_RULES 通道，与双模式纪律并存。"""
    monkeypatch.setattr(skills_api, "_loader", _FakeLoader())
    llm = _StubLLM(_StubBackend([]))
    loop = DynamicToolLoop(llm, _StubCatalog())
    state = {
        "tool_calling_mode": "prompt",
        "user_prompt": "做一个介绍你自己的ppt",
        "messages": [{"role": "user", "content": "做一个介绍你自己的ppt"}],
        "tool_results": [],
        "tool_turn_count": 0,
        "active_skill_id": "office_pptx_designer",
        "dual_rules_addon": "先读后改。",
    }
    out = _run(loop.run(state))
    assert out["final_answer"] == "done"
    kwargs = llm.orchestrate_kwargs[0]
    assert "先读后改。" in kwargs["extra_rules"]
    assert "已绑定 Skill：PPT 汇报生成规范" in kwargs["extra_rules"]


def test_merge_extra_rules_no_skill_keeps_dual_rules_only(monkeypatch):
    monkeypatch.setattr(skills_api, "_loader", _FakeLoader())
    merged = _merge_extra_rules({"dual_rules_addon": "先读后改。"})
    assert merged == "先读后改。"
    assert _merge_extra_rules({}) == ""
