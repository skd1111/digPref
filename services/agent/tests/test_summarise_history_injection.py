"""summarise 终答链路会话历史注入回归测试（BUGFIX #135）。

现象：上一轮「做一个介绍你自己的 ppt」+ 本轮「是你自己这个智能体客户端」，
模型反问「缺少明确的任务指令 / 想了解哪个方面」——说一句忘一句。
根因：前端→Rust→后端 history 链路完好（intent / planner / 工具循环都能看见），
但终答链路（responder → summarise）签名只有 intent/user_prompt/plan/results，
后端只把当轮 user_prompt 拼进 prompt，模型看不见前文。
修复：summarise 全链增加 history 参数，最近几轮原文压成简报注入终答 prompt；
L1 缓存 key 同步加入 history_brief（同问不同上下文不同 key）。

覆盖：
    - format_history_brief：角色过滤 / 单条截断 / 最近 N 条 / 空历史
    - OllamaClient.summarise：带历史注入 Recent conversation，无历史不注入
    - PrivateLLMClient.summarise：同上
    - responder MAIN_AGENT 直答路径：历史透传且去掉与当轮重复的末条
"""

from __future__ import annotations

import json

from agent.graph.nodes.responder import responder_node
from agent.llm import ollama as ollama_mod
from agent.llm import private_llm as private_mod
from agent.llm.prompts import format_history_brief

_HISTORY = [
    {"role": "user", "content": "做一个介绍你自己的ppt"},
    {"role": "assistant", "content": "我来准备这份自我介绍 PPT，计划分为五部分…"},
    # role 不在白名单 → 必须被丢弃（脏数据防御，契约不变）
    {"role": "tool", "content": "（未知角色，不应进入简报）"},
]

# 2026-08-27（根治 BUGFIX #163）：本文件原先还断言 system 消息「不应进入简报」，
# 前提是「system = 界面日志噪声」。该前提已被证伪，且那条断言会造成真实损失：
#
#   1. 客户端结构上发不出 system —— chatStore.tabContextMessages 只放行
#      user/assistant，服务端 api/chat.py::_HISTORY_ROLES 又过滤一次。
#   2. state["messages"] 里真正存在的 system 消息只有 graph/stream.py 自己注入的
#      两条：「前段对话摘要」（压缩后的旧对话）与「任务台账锚点」（已交付文件路径）。
#      这两条恰恰是跨轮上下文里信息密度最高的内容 —— 「太丑了」指哪个文件全靠它。
#
# 所以简报现在放行 system，只丢弃白名单外的未知 role。


class TestFormatHistoryBrief:
    def test_empty_history_returns_empty(self):
        assert format_history_brief(None) == ""
        assert format_history_brief([]) == ""

    def test_filters_unknown_roles(self):
        brief = format_history_brief(_HISTORY)
        assert "介绍你自己的ppt" in brief
        assert "五部分" in brief
        assert "未知角色" not in brief

    def test_keeps_system_context_messages(self):
        """stream.py 注入的「前段对话摘要」/「任务台账锚点」必须进简报。

        根治 BUGFIX #163：旧实现连带丢弃 system，跨轮追问的关键锚点（上轮交付了
        哪个文件）随之消失。
        """
        brief = format_history_brief(
            [{"role": "system", "content": "【前段对话摘要】用户在做 daide 的 PPT"}]
        )
        assert "前段对话摘要" in brief

    def test_truncates_long_message(self):
        long_msg = [{"role": "user", "content": "长" * 900}]
        brief = format_history_brief(long_msg, per_message_chars=100)
        assert len(brief) < 200
        assert "已截断" in brief

    def test_keeps_only_recent_messages(self):
        msgs = [{"role": "user", "content": f"第{i}轮"} for i in range(20)]
        brief = format_history_brief(msgs, max_messages=8)
        assert "第0轮" not in brief
        assert "第19轮" in brief

    def test_accepts_basemessage_like_objects(self):
        class _Msg:
            def __init__(self, role: str, content: str):
                self.role = role
                self.content = content

        brief = format_history_brief([_Msg("user", "你好"), _Msg("assistant", "您好")])
        assert "[user] 你好" in brief
        assert "[assistant] 您好" in brief


async def test_ollama_summarise_injects_history(monkeypatch):
    captured: list[str] = []

    async def fake_chat(self, messages, *, format=None, options=None, timeout=30.0):
        for m in messages:
            if m["role"] == "user":
                captured.append(str(m["content"]))
        return {"content": json.dumps({"answer": "ok", "sources": []})}

    monkeypatch.setattr(ollama_mod.OllamaClient, "_chat", fake_chat)
    client = ollama_mod.OllamaClient(base_url="http://127.0.0.1:11434", model="m")
    await client.summarise(
        intent="query",
        user_prompt="是你自己这个智能体客户端",
        plan=[],
        results=[],
        history=_HISTORY,
    )
    assert len(captured) == 1
    assert "Recent conversation" in captured[0]
    assert "介绍你自己的ppt" in captured[0]
    assert "未知角色" not in captured[0]


async def test_ollama_summarise_no_history_no_section(monkeypatch):
    captured: list[str] = []

    async def fake_chat(self, messages, *, format=None, options=None, timeout=30.0):
        for m in messages:
            if m["role"] == "user":
                captured.append(str(m["content"]))
        return {"content": json.dumps({"answer": "ok", "sources": []})}

    monkeypatch.setattr(ollama_mod.OllamaClient, "_chat", fake_chat)
    client = ollama_mod.OllamaClient(base_url="http://127.0.0.1:11434", model="m")
    await client.summarise(intent="query", user_prompt="你好", plan=[], results=[])
    assert "Recent conversation" not in captured[0]


async def test_private_summarise_injects_history(monkeypatch):
    captured: list[str] = []

    async def fake_chat_json(self, messages, *, response_format=None, temperature=None):
        for m in messages:
            if m["role"] == "user":
                captured.append(str(m["content"]))
        return {"answer": "ok", "sources": []}

    monkeypatch.setattr(private_mod.PrivateLLMClient, "_chat_json_with_retry", fake_chat_json)
    client = private_mod.PrivateLLMClient(base_url="http://fake.internal", api_key="k", model="m")
    await client.summarise(
        intent="query",
        user_prompt="是你自己这个智能体客户端",
        plan=[],
        results=[],
        history=_HISTORY,
    )
    assert len(captured) == 1
    assert "Recent conversation" in captured[0]
    assert "介绍你自己的ppt" in captured[0]


class _RecordingLLM:
    """记录 summarise 收到的 history，用于验证 responder 透传。"""

    def __init__(self):
        self.history_seen: list | None = None

    async def summarise(self, *, intent, user_prompt, plan, results, history=None):
        self.history_seen = history
        return "好的，我来介绍自己。", []


async def test_responder_main_agent_passes_history_without_current_dup():
    llm = _RecordingLLM()
    state = {
        "user_prompt": "是你自己这个智能体客户端",
        "messages": [
            {"role": "user", "content": "做一个介绍你自己的ppt"},
            {"role": "assistant", "content": "我来准备这份自我介绍 PPT…"},
            {"role": "user", "content": "是你自己这个智能体客户端"},
        ],
        "decompose_decision": {"decision": {"mode": "MAIN_AGENT"}},
        "trace": [],
    }
    out = await responder_node(state, llm)
    assert out["final_answer"] == "好的，我来介绍自己。"
    assert llm.history_seen is not None
    contents = [m["content"] for m in llm.history_seen]
    # 前两轮在场（上下文恢复）；当轮消息去重（避免与 User question 重复）
    assert "做一个介绍你自己的ppt" in contents
    assert contents.count("是你自己这个智能体客户端") == 0
