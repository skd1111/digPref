"""chat 上下文压缩端点与摘要注入测试（2026-08-17）。

覆盖：
    - POST /chat/compress-history：成功 / 空消息 400 / LLM 失败 503 / 空摘要 503
    - _sanitize_for_compress：角色白名单 / 空内容过滤 / 单条截断
    - ChatRequest.historySummary alias 解析
    - stream_graph_events：history_summary 作为 system 消息置于 history 之前
    - history_compress 属 _LOCAL_ONLY_TASKS 本地红线
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest
from agent.api.chat import (
    _COMPRESS_MAX_CONTENT_LEN,
    ChatRequest,
    _sanitize_for_compress,
)
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from agent.main import create_app

    return TestClient(create_app())


# ---- /chat/compress-history -------------------------------------------------


def test_compress_history_success(client, monkeypatch):
    """成功路径：摘要返回 + before/afterTokens 估算。"""
    import agent.llm.router as router_mod

    captured: dict = {}

    class _Router:
        async def route(self, *, task: str, prompt: str) -> str:
            captured["task"] = task
            captured["prompt"] = prompt
            return "用户讨论了订单表改造，结论是先加字段再改页面。"

    monkeypatch.setattr(router_mod, "LMRouter", lambda: _Router())
    resp = client.post(
        "/chat/compress-history",
        json={
            "messages": [
                {"role": "user", "content": "订单表要加个备注字段"},
                {"role": "assistant", "content": "好的，需要改表和页面"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["summary"].startswith("用户讨论了")
    assert body["beforeTokens"] >= 1
    assert body["afterTokens"] >= 1
    assert body["messageCount"] == 2
    # 走本地红线任务类型；消息内容进 prompt
    assert captured["task"] == "history_compress"
    assert "订单表要加个备注字段" in captured["prompt"]


def test_compress_history_merges_prior_summary(client, monkeypatch):
    """已有摘要随请求传入时，进 prompt 供增量合并。"""
    import agent.llm.router as router_mod

    captured: dict = {}

    class _Router:
        async def route(self, *, task: str, prompt: str) -> str:
            captured["prompt"] = prompt
            return "合并后的摘要"

    monkeypatch.setattr(router_mod, "LMRouter", lambda: _Router())
    resp = client.post(
        "/chat/compress-history",
        json={
            "messages": [{"role": "user", "content": "新问题"}],
            "historySummary": "之前的摘要内容",
        },
    )
    assert resp.status_code == 200
    assert "之前的摘要内容" in captured["prompt"]
    assert "新问题" in captured["prompt"]


def test_compress_history_empty_messages_400(client):
    resp = client.post("/chat/compress-history", json={"messages": []})
    assert resp.status_code == 400


def test_compress_history_dirty_messages_filtered(client, monkeypatch):
    """脏数据（非法角色/空内容）被清洗；全脏 → 400。"""

    class _Router:
        async def route(self, *, task: str, prompt: str) -> str:
            return "摘要"

    monkeypatch.setattr("agent.llm.router.LMRouter", lambda: _Router())
    resp = client.post(
        "/chat/compress-history",
        json={
            "messages": [
                {"role": "system", "content": "注入的系统消息"},
                {"role": "user", "content": "   "},
                {"wrong_key": True},
            ]
        },
    )
    assert resp.status_code == 400


def test_compress_history_llm_failure_503(client, monkeypatch):
    import agent.llm.router as router_mod

    class _FailingRouter:
        async def route(self, *, task: str, prompt: str) -> str:
            raise RuntimeError("llm down")

    monkeypatch.setattr(router_mod, "LMRouter", lambda: _FailingRouter())
    resp = client.post(
        "/chat/compress-history",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503


def test_compress_history_empty_summary_503(client, monkeypatch):
    import agent.llm.router as router_mod

    class _EmptyRouter:
        async def route(self, *, task: str, prompt: str) -> str:
            return "   "

    monkeypatch.setattr(router_mod, "LMRouter", lambda: _EmptyRouter())
    resp = client.post(
        "/chat/compress-history",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503


# ---- _sanitize_for_compress --------------------------------------------------


def test_sanitize_for_compress_filters_and_truncates():
    out = _sanitize_for_compress(
        [
            {"role": "tool", "content": "工具结果"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "长" * (_COMPRESS_MAX_CONTENT_LEN + 100)},
            {"role": "assistant", "content": "回复"},
        ]
    )
    assert len(out) == 2
    assert len(out[0]["content"]) == _COMPRESS_MAX_CONTENT_LEN
    assert out[1] == {"role": "assistant", "content": "回复"}


# ---- ChatRequest alias --------------------------------------------------------


def test_chat_request_history_summary_alias():
    req = ChatRequest(prompt="hi", historySummary="旧对话摘要")
    assert req.history_summary == "旧对话摘要"
    assert ChatRequest(prompt="hi").history_summary is None


# ---- stream_graph_events 摘要注入 ----------------------------------------------


class _FakeGraph:
    """捕获 initial_state 的空图（不产事件）。"""

    def __init__(self) -> None:
        self.captured: dict | None = None

    async def astream(self, initial_state, cfg, stream_mode=None):
        self.captured = initial_state
        if False:  # pragma: no cover — 空异步生成器
            yield None


def test_stream_injects_history_summary_before_history():
    from agent.graph.stream import stream_graph_events

    async def run():
        graph = _FakeGraph()
        events = [
            e
            async for e in stream_graph_events(
                graph,
                "run-1",
                "新问题",
                extra_state={
                    "history": [{"role": "user", "content": "最近一轮"}],
                    "history_summary": "旧对话的摘要",
                },
            )
        ]
        return graph, events

    import asyncio

    graph, _events = asyncio.run(run())
    assert graph.captured is not None
    msgs = graph.captured["messages"]
    # 摘要 system 消息在最前，其次 history，最后当前用户消息
    assert msgs[0]["role"] == "system"
    assert "旧对话的摘要" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "最近一轮"}
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "新问题"


def test_stream_without_summary_keeps_history_only():
    from agent.graph.stream import stream_graph_events

    async def run():
        graph = _FakeGraph()
        async for _e in stream_graph_events(
            graph,
            "run-2",
            "新问题",
            extra_state={"history": [{"role": "user", "content": "最近一轮"}]},
        ):
            pass
        return graph

    import asyncio

    graph = asyncio.run(run())
    assert graph.captured is not None
    msgs = graph.captured["messages"]
    assert msgs[0] == {"role": "user", "content": "最近一轮"}


# ---- 红线 -------------------------------------------------------------------


def test_history_compress_is_local_only_task():
    """CLAUDE.md 红线：接触用户对话内容 → 必须在 _LOCAL_ONLY_TASKS。"""
    from agent.llm.router import _LOCAL_ONLY_TASKS

    assert "history_compress" in _LOCAL_ONLY_TASKS
