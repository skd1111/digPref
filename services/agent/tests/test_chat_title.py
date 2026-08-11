"""Tests for `/chat/summarize-title` and history sanitization (2026-08-07).

覆盖：
    - mock 模式：直接用用户问题截断生成标题（不返回 biznav JSON 垃圾串）
    - LLM 失败：返回 `{"title": ""}`，前端保留截断标题兜底
    - assistant 回复节选被拼进摘要 prompt
    - `_sanitize_history`：角色白名单 / 空内容过滤 / 长度截断 / 消息数上限
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest
from agent.api.chat import (
    _HISTORY_MAX_CONTENT_LEN,
    _HISTORY_MAX_MESSAGES,
    _sanitize_history,
)
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from agent.main import create_app

    return TestClient(create_app())


# ---- /chat/summarize-title -------------------------------------------------


def test_summarize_title_mock_mode_uses_user_prompt(client, monkeypatch):
    monkeypatch.setenv("EAIDE_LLM_BACKEND", "mock")
    resp = client.post(
        "/chat/summarize-title",
        json={
            "userPrompt": "帮我查一下订单表的数据",
            "assistantReply": "已查到 3 条记录",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"title": "帮我查一下订单表的数据"}


def test_summarize_title_includes_assistant_snippet(client, monkeypatch):
    """assistant 回复节选要进摘要 prompt（摘要质量依赖双方上下文）。"""
    import agent.llm.router as router_mod

    captured: dict = {}

    class _Router:
        _mock_mode = False

        async def _route_local_first(self, *, task: str, prompt: str) -> str:
            captured["prompt"] = prompt
            return "短标题"

    monkeypatch.setattr(router_mod, "LMRouter", lambda: _Router())
    resp = client.post(
        "/chat/summarize-title",
        json={"userPrompt": "怎么改", "assistantReply": "先改配置再重启"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"title": "短标题"}
    assert "AI 回复节选" in captured["prompt"]
    assert "先改配置再重启" in captured["prompt"]


def test_summarize_title_llm_failure_returns_empty(client, monkeypatch):
    """任何 LLM 失败都要返回空 title，前端保留截断标题，不阻塞主链路。"""
    import agent.llm.router as router_mod

    class _FailingRouter:
        _mock_mode = False

        async def _route_local_first(self, *, task: str, prompt: str) -> str:
            raise RuntimeError("llm down")

    monkeypatch.setattr(router_mod, "LMRouter", lambda: _FailingRouter())
    resp = client.post("/chat/summarize-title", json={"userPrompt": "问题"})
    assert resp.status_code == 200
    assert resp.json() == {"title": ""}


# ---- _sanitize_history -----------------------------------------------------


def test_sanitize_history_filters_roles_and_blanks():
    raw = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "工具返回"},
        {"role": "assistant", "content": "回答"},
        "not-a-dict",
        None,
        {"role": "user", "content": "   "},
    ]
    assert _sanitize_history(raw) == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "回答"},
    ]


def test_sanitize_history_truncates_content_and_caps_count():
    raw = [
        {"role": "user", "content": "长" * 5000},
        *[{"role": "assistant", "content": f"m{i}"} for i in range(30)],
    ]
    out = _sanitize_history(raw)
    assert len(out) == _HISTORY_MAX_MESSAGES
    assert all(len(m["content"]) <= _HISTORY_MAX_CONTENT_LEN for m in out)
    # 最旧一条 user（超长）被挤出，且内容被截断
    assert out[0]["role"] == "assistant"
    assert out[-1]["content"] == "m29"


def test_sanitize_history_truncates_long_content():
    out = _sanitize_history([{"role": "user", "content": "长" * 5000}])
    assert len(out) == 1
    assert out[0]["content"] == "长" * _HISTORY_MAX_CONTENT_LEN
