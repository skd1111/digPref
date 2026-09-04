"""知识库召回注入终答链路回归测试（根因修复 2026-09-04）。

现象：用户问「找一下对公转账汇兑的规章制度」，hybrid_search bm25=80 命中、
返回 hits=20，但 agent 反而弹 A/B/C 澄清、继而用 shell 全盘翻找卡死。
根因：rag_retrieve 把召回写进 state.system_prompt_addon 后，整个终答链路
（summarise / summarise_stream / 各后端）从未读取它——模型作答时根本看不到
知识库内容。修复：summarise 全链增加 rag_context 参数并注入 prompt；responder
在 _summarise_maybe_stream 单点把 system_prompt_addon 透传进去；L1 缓存 key
同步纳入 rag_context（同问不同召回不同 key）。

覆盖：
    - format_rag_block：空召回返 ""、非空带据库作答/溯源纪律
    - OllamaClient / PrivateLLMClient.summarise：注入知识库参考段，空则不注入
    - build_summarise_messages：流式路径同源注入
    - responder MAIN_AGENT 直答：system_prompt_addon → rag_context 透传
    - build_response_cache_key：rag_context 不同 → key 不同
"""

from __future__ import annotations

import json

from agent.graph.nodes.responder import responder_node
from agent.llm import ollama as ollama_mod
from agent.llm import private_llm as private_mod
from agent.llm.normalize import build_response_cache_key
from agent.llm.prompts import format_rag_block
from agent.llm.router import build_summarise_messages

_RAG = "## 知识库参考（本地混合检索）\n[1] 对公转账汇兑管理办法（第2页）：单笔超过 50 万需复核。"


class TestFormatRagBlock:
    def test_empty_returns_empty(self):
        assert format_rag_block("") == ""
        assert format_rag_block(None) == ""
        assert format_rag_block("   \n ") == ""

    def test_non_empty_wraps_with_discipline(self):
        block = format_rag_block(_RAG)
        assert _RAG in block
        # 据库作答 + 溯源 + 不要翻文件系统 的纪律在场
        assert "优先依据下列资料作答" in block
        assert "编号标注来源" in block
        assert "文件系统命令" in block


async def test_ollama_summarise_injects_rag(monkeypatch):
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
        user_prompt="对公转账汇兑的规章制度",
        plan=[],
        results=[],
        rag_context=_RAG,
    )
    assert len(captured) == 1
    assert "知识库参考" in captured[0]
    assert "对公转账汇兑管理办法" in captured[0]


async def test_ollama_summarise_no_rag_no_section(monkeypatch):
    captured: list[str] = []

    async def fake_chat(self, messages, *, format=None, options=None, timeout=30.0):
        for m in messages:
            if m["role"] == "user":
                captured.append(str(m["content"]))
        return {"content": json.dumps({"answer": "ok", "sources": []})}

    monkeypatch.setattr(ollama_mod.OllamaClient, "_chat", fake_chat)
    client = ollama_mod.OllamaClient(base_url="http://127.0.0.1:11434", model="m")
    await client.summarise(intent="query", user_prompt="你好", plan=[], results=[])
    assert "知识库参考" not in captured[0]


async def test_private_summarise_injects_rag(monkeypatch):
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
        user_prompt="对公转账汇兑的规章制度",
        plan=[],
        results=[],
        rag_context=_RAG,
    )
    assert len(captured) == 1
    assert "知识库参考" in captured[0]


def test_build_summarise_messages_injects_rag():
    msgs = build_summarise_messages("query", "对公转账汇兑的规章制度", [], [], None, _RAG)
    user = next(m["content"] for m in msgs if m["role"] == "user")
    assert "知识库参考" in user
    assert "对公转账汇兑管理办法" in user


def test_build_summarise_messages_no_rag_no_section():
    msgs = build_summarise_messages("query", "你好", [], [], None, "")
    user = next(m["content"] for m in msgs if m["role"] == "user")
    assert "知识库参考" not in user


class _RecordingLLM:
    """记录 summarise 收到的 rag_context，用于验证 responder 透传。"""

    def __init__(self):
        self.rag_seen: str | None = None

    async def summarise(self, *, intent, user_prompt, plan, results, history=None, rag_context=""):
        self.rag_seen = rag_context
        return "根据知识库……", []


async def test_responder_main_agent_passes_rag_context():
    llm = _RecordingLLM()
    state = {
        "user_prompt": "知识库里没有吗",
        "messages": [{"role": "user", "content": "知识库里没有吗"}],
        "system_prompt_addon": _RAG,
        "decompose_decision": {"decision": {"mode": "MAIN_AGENT"}},
        "trace": [],
    }
    out = await responder_node(state, llm)
    assert out["final_answer"] == "根据知识库……"
    assert llm.rag_seen == _RAG


async def test_responder_no_addon_no_rag_context():
    """system_prompt_addon 为空时不注入 rag_context（向后兼容缺该参数的替身）。"""
    llm = _RecordingLLM()
    state = {
        "user_prompt": "你好",
        "messages": [{"role": "user", "content": "你好"}],
        "decompose_decision": {"decision": {"mode": "MAIN_AGENT"}},
        "trace": [],
    }
    await responder_node(state, llm)
    assert llm.rag_seen == ""


def test_cache_key_sensitive_to_rag_context():
    base = dict(
        task_kind="summarise",
        intent="query",
        user_prompt="对公转账汇兑的规章制度",
        plan=[],
        results=[],
        history_brief="",
    )
    k_empty = build_response_cache_key(**base, rag_context="")
    k_rag = build_response_cache_key(**base, rag_context=_RAG)
    assert k_empty != k_rag
    # 相同召回 → 相同 key（稳定）
    assert build_response_cache_key(**base, rag_context=_RAG) == k_rag
