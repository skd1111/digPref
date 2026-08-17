"""summarise 当前时间注入回归测试（BUGFIX #112）。

现象：用户问「今天是几月几号」，模型回答「今天是10月5日」（实际 2026-08-17）。
根因：终答链路（summarise）不注入当前时间，本地模型对「今天」无可靠感知，
凭训练知识编造日期。修复：两个终答后端（ollama / private）的 summarise 用户
消息统一注入 `Current time:` 行，summarise.md §5.1 规定其为唯一时间基准。

覆盖：
    - current_time_text() 返回本地今天（含星期）
    - OllamaClient.summarise 用户消息含 Current time 注入
    - PrivateLLMClient.summarise 用户消息含 Current time 注入
    - summarise.md 时间纪律条款在场
"""

from __future__ import annotations

import json
from datetime import datetime

from agent.llm import ollama as ollama_mod
from agent.llm import private_llm as private_mod
from agent.llm.prompts import current_time_text, load_prompt


def test_current_time_text_is_today_with_weekday():
    text = current_time_text()
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    assert today in text
    assert "星期" in text


async def test_ollama_summarise_injects_current_time(monkeypatch):
    captured: list[str] = []

    async def fake_chat(self, messages, *, format=None, options=None, timeout=30.0):
        for m in messages:
            if m["role"] == "user":
                captured.append(str(m["content"]))
        return {"content": json.dumps({"answer": "ok", "sources": []})}

    monkeypatch.setattr(ollama_mod.OllamaClient, "_chat", fake_chat)
    client = ollama_mod.OllamaClient(base_url="http://127.0.0.1:11434", model="m")
    answer, _ = await client.summarise(
        intent="query", user_prompt="今天是几月几号", plan=[], results=[]
    )
    assert answer == "ok"
    assert len(captured) == 1
    assert "Current time:" in captured[0]
    assert datetime.now().astimezone().strftime("%Y-%m-%d") in captured[0]


async def test_private_summarise_injects_current_time(monkeypatch):
    captured: list[str] = []

    async def fake_chat_json(self, messages, *, response_format=None, temperature=None):
        for m in messages:
            if m["role"] == "user":
                captured.append(str(m["content"]))
        return {"answer": "ok", "sources": []}

    monkeypatch.setattr(private_mod.PrivateLLMClient, "_chat_json_with_retry", fake_chat_json)
    client = private_mod.PrivateLLMClient(base_url="http://fake.internal", api_key="k", model="m")
    answer, _ = await client.summarise(
        intent="query", user_prompt="今天是几月几号", plan=[], results=[]
    )
    assert answer == "ok"
    assert len(captured) == 1
    assert "Current time:" in captured[0]
    assert datetime.now().astimezone().strftime("%Y-%m-%d") in captured[0]


def test_summarise_prompt_has_time_discipline():
    """提示词侧红线：禁止从训练知识回忆日期，以注入时间为唯一基准。"""
    prompt = load_prompt("summarise")
    assert "Current time" in prompt
    assert "Time discipline" in prompt
