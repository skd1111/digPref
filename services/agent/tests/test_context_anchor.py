"""BUGFIX #140 —— 上下文关联三连修：选项回复任务背景 / 身份锚点 / 闲聊守卫。

真实翻车会话（2026-08-25，4efcaf9b）：
  ① 点选项 A（对外宣传/路演）后，模型把「介绍你自己的 PPT」漂成「您要宣传的
     产品是什么」—— 选项回复文本只有「问题→选择」，不含原任务目标；
  ② 「介绍你自己」的 PPT 大纲署名「关于我 —— MiniMax-M3」—— 系统提示词无
     智能体身份锚点，模型拿底层模型名当了自我介绍主体；
  ③ 「不要带上模型，你是智能体工具」被关键词启发式判成 chitchat（本地意图
     模型缺席，analyze_intent 退化 mock/plain），responder 模板直回吞掉纠正。
"""

from __future__ import annotations

import asyncio

import pytest
from agent.graph.nodes.responder import responder_node
from agent.tools.loop import _NATIVE_SYSTEM_PROMPT, DynamicToolLoop


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- ① 选项回复注入原任务背景 ------------------------------------------------


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

    async def resolve_native_backend(self):
        return ("cloud", self._backend)


class _StubCatalog:
    async def definitions(self, names=None):
        return []

    async def execute(self, name, args, state):
        return {"ok": True}


def test_option_reply_seeds_original_task_background():
    """[回答确认问题] 开头的输入 → 首轮注入 [任务背景]（最近一条原任务）。"""
    backend = _StubBackend([{"content": "继续生成中。", "tool_calls": []}])
    loop = DynamicToolLoop(_StubLLM(backend), _StubCatalog())
    state = {
        "tool_calling_mode": "native",
        "user_prompt": (
            "[回答确认问题]\n1. 请选择 → 对外宣传/路演（突出产品价值）\n请按以上选择继续。"
        ),
        "messages": [
            {"role": "user", "content": "做一个介绍下你自己（EAIDE 企业 AI 助理）的ppt"},
            {"role": "assistant", "content": "这份 PPT 主要的用途和受众是哪种？"},
        ],
        "tool_results": [],
        "tool_turn_count": 0,
    }
    out = _run(loop.run(state))
    assert out["final_answer"] == "继续生成中。"

    seeded_user = [m for m in backend.request_messages[0] if m["role"] == "user"][-1]
    content = seeded_user["content"]
    assert "[任务背景]" in content
    assert "做一个介绍下你自己（EAIDE 企业 AI 助理）的ppt" in content
    assert "[回答确认问题]" in content  # 原输入保留


def test_normal_prompt_gets_no_background_prefix():
    """普通输入不拼任务背景（不误伤常规链路）。"""
    backend = _StubBackend([{"content": "done", "tool_calls": []}])
    loop = DynamicToolLoop(_StubLLM(backend), _StubCatalog())
    state = {
        "tool_calling_mode": "native",
        "user_prompt": "今天几号",
        "messages": [{"role": "user", "content": "做一个ppt"}],
        "tool_results": [],
        "tool_turn_count": 0,
    }
    _run(loop.run(state))
    seeded_user = [m for m in backend.request_messages[0] if m["role"] == "user"][-1]
    assert "[任务背景]" not in seeded_user["content"]


# ---- ② 身份锚点 --------------------------------------------------------------


def test_native_system_prompt_pins_agent_identity():
    """native 循环系统提示词必须锚定智能体身份，禁止底层模型名当主体。"""
    assert "EAIDE 企业 AI 助理" in _NATIVE_SYSTEM_PROMPT
    assert "严禁把底层模型" in _NATIVE_SYSTEM_PROMPT


# ---- ③ 会话中段闲聊不再模板直回 ----------------------------------------------


class _SummariseStub:
    def __init__(self, answer="明白了，我会按智能体身份来调整。", raise_exc=None):
        self.answer = answer
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    async def summarise(self, *, intent, user_prompt, plan, results, history=None):
        self.calls.append({"intent": intent, "history": history})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.answer, []


def _chitchat_state(n_msgs: int) -> dict:
    return {
        "intent": "chitchat",
        "user_prompt": "不要带上模型，你是智能体工具",
        "messages": [
            *[
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"第 {i} 轮"}
                for i in range(n_msgs)
            ]
        ],
        "plan": [],
        "tool_results": [],
    }


def test_first_turn_chitchat_keeps_canned():
    """首轮问候（历史仅当轮一条）保留零 LLM 模板直回。"""
    llm = _SummariseStub()
    out = _run(responder_node(_chitchat_state(1), llm))
    assert "EAIDE 企业 AI 助理" in out["final_answer"]
    assert llm.calls == []  # 零 LLM


@pytest.mark.asyncio
async def test_mid_session_chitchat_answers_with_history():
    """会话中段被判闲聊（启发式误判高发区）→ 带历史正常回答，不吞用户纠正。"""
    llm = _SummariseStub()
    out = await responder_node(_chitchat_state(4), llm)
    assert out["final_answer"] == "明白了，我会按智能体身份来调整。"
    assert len(llm.calls) == 1
    assert llm.calls[0]["history"]  # 历史真注入（#135 链路）


@pytest.mark.asyncio
async def test_mid_session_chitchat_falls_back_to_canned_when_llm_down():
    """全链不可用 → 回退模板，宁模板不可空白。"""
    llm = _SummariseStub(raise_exc=RuntimeError("no backend"))
    out = await responder_node(_chitchat_state(4), llm)
    assert "EAIDE 企业 AI 助理" in out["final_answer"]
