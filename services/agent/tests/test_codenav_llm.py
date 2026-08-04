"""test_codenav_llm.py —— Phase 2F LLM 客户端测试（环境变量配置 + mock fallback）。"""
from __future__ import annotations

import json

import pytest

from agent.codenav.llm_client import (
    CodenavLLMClient,
    _coerce_infer,
    _parse_infer_json,
    get_default_client,
    reset_default_client,
)


# ---------------------------------------------------------------------------
# 配置 + 单例
# ---------------------------------------------------------------------------

def test_unconfigured_returns_false():
    c = CodenavLLMClient(base_url="", model="", api_key="")
    assert c.configured is False


def test_partially_configured_returns_false():
    c = CodenavLLMClient(base_url="http://x", model="", api_key="")
    assert c.configured is False


def test_fully_configured_returns_true():
    c = CodenavLLMClient(base_url="http://x", model="m", api_key="k")
    assert c.configured is True


def test_default_client_reads_env(monkeypatch):
    monkeypatch.setenv("EAIDE_CODENAV_LLM_BASE_URL", "http://env-host:1234/v1")
    monkeypatch.setenv("EAIDE_CODENAV_LLM_MODEL", "env-model")
    monkeypatch.setenv("EAIDE_CODENAV_LLM_API_KEY", "env-key")
    reset_default_client()
    c = get_default_client()
    assert c.base_url == "http://env-host:1234/v1"
    assert c.model == "env-model"
    assert c.api_key == "env-key"
    reset_default_client()


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

def test_parse_infer_json_clean():
    raw = '{"file": "a.py", "line": 12, "confidence": 0.8, "reasoning": "x"}'
    out = _parse_infer_json(raw)
    assert out == {"file": "a.py", "line": 12, "confidence": 0.8, "reasoning": "x"}


def test_parse_infer_json_with_fence():
    raw = "```json\n{\"file\": \"a.py\", \"line\": 1, \"confidence\": 0.5, \"reasoning\": \"y\"}\n```"
    out = _parse_infer_json(raw)
    assert out["file"] == "a.py"
    assert out["line"] == 1


def test_parse_infer_json_with_extra_text():
    raw = (
        "我推断定义在：\n"
        "{\"file\": \"b.py\", \"line\": 5, \"confidence\": 0.6, \"reasoning\": \"z\"}\n"
        "以上是我的推断。"
    )
    out = _parse_infer_json(raw)
    assert out["file"] == "b.py"


def test_parse_infer_json_invalid_returns_none():
    assert _parse_infer_json("not json at all") is None


def test_coerce_infer_normalises():
    out = _coerce_infer({"file": "x", "line": "42", "confidence": "0.9"})
    assert out["line"] == 42
    assert out["confidence"] == 0.9


def test_coerce_infer_clamps_confidence():
    out = _coerce_infer({"file": "x", "confidence": 1.5})
    assert out["confidence"] == 1.0


# ---------------------------------------------------------------------------
# 推断/解释调用：未配置 → None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_infer_unconfigured_returns_none():
    c = CodenavLLMClient()
    assert await c.infer_definition("foo", "f.py", "ctx") is None


@pytest.mark.asyncio
async def test_explain_unconfigured_returns_none():
    c = CodenavLLMClient()
    assert await c.explain_symbol("foo", "f.py", 1, "ctx") is None


# ---------------------------------------------------------------------------
# HTTP 调用成功路径（用 httpx mock）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_infer_calls_openai_compatible(monkeypatch):
    """Mock httpx 验证：base_url/model/api_key 正确拼到请求。"""
    c = CodenavLLMClient(
        base_url="http://llm.test/v1",
        model="test-model",
        api_key="test-key",
        timeout_s=5.0,
    )

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "file": "/abs/path/foo.py", "line": 10, "confidence": 0.85,
                "reasoning": "因为类名匹配",
            })}}]}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResp()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    out = await c.infer_definition("foo", "/current.py", "上下文内容")
    assert out is not None
    assert out["file"] == "/abs/path/foo.py"
    assert out["line"] == 10
    assert captured["url"] == "http://llm.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "test-model"


@pytest.mark.asyncio
async def test_explain_returns_text(monkeypatch):
    c = CodenavLLMClient(base_url="http://llm.test/v1", model="m", api_key="k")

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "**foo** 是核心入口。"}}]}

    async def fake_post(self, url, json=None, headers=None):
        return FakeResp()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    out = await c.explain_symbol("foo", "/current.py", 5, "ctx")
    assert out == "**foo** 是核心入口。"


@pytest.mark.asyncio
async def test_llm_call_failure_returns_none(monkeypatch):
    """HTTP 错误时返回 None（调用方走 mock 兜底）。"""
    c = CodenavLLMClient(base_url="http://llm.test/v1", model="m", api_key="k")

    async def fake_post(self, url, json=None, headers=None):
        raise RuntimeError("connection refused")

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    assert await c.infer_definition("foo", "f.py", "ctx") is None
    assert await c.explain_symbol("foo", "f.py", 1, "ctx") is None
