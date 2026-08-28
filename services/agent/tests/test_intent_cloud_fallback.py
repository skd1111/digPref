"""BUGFIX #141 —— 意图分析动态降级：本地/内网不可用 → 云端 → 才轮到启发式。

2026-08-25 实测：本地 Ollama 未配置、内网未配置时，analyze_intent 链
（原为 ollama → private → plain）直接掉到关键词启发式，把任务中段的纠偏
短句「不要带上模型，你是智能体工具」误判闲聊 → 模板直回吞掉用户纠正。
本地红线自 2026-08-05 起语义即「本地优先」，允许降级 —— 云端已配置就必须
接住，绝不直接掉启发式。
"""

from __future__ import annotations

import asyncio

import pytest
from agent.llm.ollama import OllamaUnavailableError
from agent.llm.router import LMRouter


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _DeadOllama:
    """本地 Ollama 未配置/未启动。"""

    async def analyze_intent(self, text, history=None, page_context=""):
        raise OllamaUnavailableError("端侧 Ollama 未配置，跳过探测")

    async def classify_intent(self, text):
        raise OllamaUnavailableError("端侧 Ollama 未配置，跳过探测")


class _CloudStub:
    base_url = "https://cloud.example/v1"

    def __init__(self, raise_exc=None):
        self.raise_exc = raise_exc
        self.calls: list[str] = []

    async def analyze_intent(self, text, history=None, page_context=""):
        self.calls.append(text)
        if self.raise_exc is not None:
            raise self.raise_exc
        return {
            "intent": "query",
            "intent_category": "knowledge_qa",
            "need_tool": True,
            "need_clarification": False,
            "risk_level": "low",
            "confidence": 0.9,
            "backend": "cloud",
            "rewritten_query": text,
        }


def _make_router(monkeypatch, cloud) -> LMRouter:
    r = LMRouter()
    monkeypatch.setattr(r, "ollama", _DeadOllama())
    monkeypatch.setattr(r, "private", None)

    async def _build():
        return cloud

    monkeypatch.setattr(r, "_build_cloud_client", _build)
    return r


def test_intent_falls_back_to_cloud_when_local_and_private_down(monkeypatch):
    """本地 + 内网都不可用 → 云端接住，绝不掉启发式。"""
    cloud = _CloudStub()
    r = _make_router(monkeypatch, cloud)
    out = _run(r.analyze_intent("不要带上模型，你是智能体工具"))
    assert cloud.calls == ["不要带上模型，你是智能体工具"]
    assert out["intent"] == "query"  # 云端结构化判定，不是启发式 chitchat
    assert out["backend"] == "cloud"


def test_intent_plain_only_when_no_backend_at_all(monkeypatch):
    """云端也没配置 → 才回退 plain 启发式（链条永不抛异常）。"""
    r = _make_router(monkeypatch, None)
    out = _run(r.analyze_intent("你好"))
    assert out["backend"] == "plain"


def test_intent_cloud_failure_degrades_to_plain(monkeypatch):
    """云端调用失败 → 继续降 plain，不阻断、不抛异常。"""
    cloud = _CloudStub(raise_exc=RuntimeError("cloud 500"))
    r = _make_router(monkeypatch, cloud)
    out = _run(r.analyze_intent("查询订单状态"))
    assert cloud.calls == ["查询订单状态"]
    assert out["backend"] == "plain"


@pytest.mark.asyncio
async def test_ollama_first_still_preferred(monkeypatch):
    """本地可用时仍本地优先（动态降级不改变优先级）。"""

    class _LiveOllama:
        async def analyze_intent(self, text, history=None, page_context=""):
            return {"intent": "query", "backend": "ollama", "confidence": 0.9}

    cloud = _CloudStub()
    r = _make_router(monkeypatch, cloud)
    monkeypatch.setattr(r, "ollama", _LiveOllama())
    out = await r.analyze_intent("查询订单")
    assert out["backend"] == "ollama"
    assert cloud.calls == []  # 云端不被调用
