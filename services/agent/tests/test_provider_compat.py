"""BUGFIX #159 —— 多厂商协议兼容改造回归测试（2026-08-27）。

背景：对照 GitHub 上主流适配层项目（LiteLLM / unify-llm / Hermes Agent）的
定制改造清单逐条核查后，补齐三块缺口：

1. json_schema + strict 是 OpenAI 专属能力，内网/多数兼容后端直接 400 ——
   客户端层必须做「去参降级重发」而不是整任务失败；
2. max_tokens 与 max_completion_tokens 命名分裂 —— 400 时换名重试；
3. 429 限流：客户端读 Retry-After 同后端退避重试一次；fallback 链对 429 /
   请求被拒类错误不计熔断失败（后端无过错，限流窗口内 3 次请求不应触发熔断）。

覆盖：
    - _unsupported_param / _retry_after_seconds / _adapt_payload 纯函数
    - _post_chat：400 response_format → 去参重发成功
    - _post_chat：400 max_tokens → 换名重发成功
    - _post_chat：429 Retry-After=0 → 同后端重试一次成功；持续 429 不死循环
    - _classify_http_error：400 tool id / 参数不支持 / 429 / 5xx / 普通 400 分流
    - with_fallback：429 不计熔断失败、照常切下一级
"""

from __future__ import annotations

import json

import httpx
import pytest
from agent.llm.circuit_breaker import CircuitBreakerRegistry
from agent.llm.fallback import (
    LLMBackendError,
    LLMParamUnsupportedError,
    LLMRateLimitError,
    LLMToolIdMismatchError,
    LLMUnavailableError,
    with_fallback,
)
from agent.llm.private_llm import (
    PrivateLLMClient,
    _retry_after_seconds,
    _unsupported_param,
)
from agent.llm.router import _classify_http_error

_URL = "http://fake.llm.local/chat/completions"


def _resp(status: int, body: dict | None = None, text: str = "", headers=None) -> httpx.Response:
    request = httpx.Request("POST", _URL)
    if body is not None:
        content = json.dumps(body).encode()
    else:
        content = text.encode()
    return httpx.Response(status, content=content, request=request, headers=headers or {})


def _ok_body() -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": '{"answer": "ok"}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class _FakeClient:
    """按脚本逐次返回响应，并快照每次请求的 payload。"""

    def __init__(self, responses: list[httpx.Response]):
        self.responses = list(responses)
        self.payloads: list[dict] = []

    async def post(self, url, json=None, timeout=None):
        self.payloads.append(dict(json or {}))
        return self.responses.pop(0)


# ---- 纯函数 ------------------------------------------------------------------


def test_unsupported_param_response_format():
    body = '{"error": "response_format.json_schema is not supported"}'
    assert _unsupported_param(body, {"response_format": {"type": "json_schema"}}) == (
        "response_format"
    )
    # payload 里没有该参数时不建议适配
    assert _unsupported_param(body, {"model": "m"}) is None


def test_unsupported_param_max_tokens():
    body = "Unsupported parameter: max_tokens. Use max_completion_tokens instead."
    assert _unsupported_param(body, {"max_tokens": 512}) == "max_tokens"


def test_unsupported_param_unknown():
    assert _unsupported_param("context length exceeded", {"max_tokens": 1}) is None


def test_retry_after_seconds_variants():
    assert _retry_after_seconds(_resp(429, headers={"Retry-After": "1"})) == 1.0
    assert _retry_after_seconds(_resp(429, headers={"Retry-After": "0"})) == 0.0
    # 超上限（3s）→ 不重试，直接上抛走降级链
    assert _retry_after_seconds(_resp(429, headers={"Retry-After": "60"})) is None
    # 缺失 / 非法
    assert _retry_after_seconds(_resp(429)) is None
    assert _retry_after_seconds(_resp(429, headers={"Retry-After": "abc"})) is None


def test_adapt_payload_drops_response_format():
    payload = {"model": "m", "max_tokens": 100, "response_format": {"type": "json_schema"}}
    adapted = PrivateLLMClient._adapt_payload(payload, "response_format")
    assert "response_format" not in adapted
    assert adapted["max_tokens"] == 100
    assert "response_format" in payload  # 原对象不变


def test_adapt_payload_swaps_max_tokens():
    payload = {"model": "m", "max_tokens": 100}
    adapted = PrivateLLMClient._adapt_payload(payload, "max_tokens")
    assert "max_tokens" not in adapted
    assert adapted["max_completion_tokens"] == 100


# ---- _post_chat：400 参数适配重试 ----------------------------------------------


@pytest.mark.asyncio
async def test_post_chat_drops_unsupported_response_format():
    """后端拒绝 json_schema → 去掉 response_format 重发一次成功。"""
    fake = _FakeClient(
        [
            _resp(400, text='{"error": "response_format json_schema not supported"}'),
            _resp(200, body=_ok_body()),
        ]
    )
    c = PrivateLLMClient(base_url="http://fake.llm.local", api_key="", model="m")
    body = await c._post_chat(
        {"model": "m", "messages": [], "response_format": {"type": "json_schema"}},
        client=fake,
    )
    assert body["choices"][0]["message"]["content"] == '{"answer": "ok"}'
    assert len(fake.payloads) == 2
    assert "response_format" in fake.payloads[0]
    assert "response_format" not in fake.payloads[1]  # 重发时已降级


@pytest.mark.asyncio
async def test_post_chat_swaps_max_tokens_on_400():
    fake = _FakeClient(
        [
            _resp(400, text="'max_tokens' is not supported, use max_completion_tokens"),
            _resp(200, body=_ok_body()),
        ]
    )
    c = PrivateLLMClient(base_url="http://fake.llm.local", api_key="", model="m")
    await c._post_chat({"model": "m", "messages": [], "max_tokens": 256}, client=fake)
    assert fake.payloads[1].get("max_completion_tokens") == 256
    assert "max_tokens" not in fake.payloads[1]


@pytest.mark.asyncio
async def test_post_chat_adapts_only_once():
    """适配一次后仍 400 → 不再重试，原样上抛。"""
    fake = _FakeClient(
        [
            _resp(400, text='{"error": "response_format not supported"}'),
            _resp(400, text='{"error": "response_format not supported"}'),
        ]
    )
    c = PrivateLLMClient(base_url="http://fake.llm.local", api_key="", model="m")
    with pytest.raises(httpx.HTTPStatusError):
        await c._post_chat(
            {"model": "m", "messages": [], "response_format": {"type": "json_schema"}},
            client=fake,
        )
    assert len(fake.payloads) == 2


# ---- _post_chat：429 Retry-After ----------------------------------------------


@pytest.mark.asyncio
async def test_post_chat_429_retries_with_retry_after():
    fake = _FakeClient(
        [
            _resp(429, text="rate limited", headers={"Retry-After": "0"}),
            _resp(200, body=_ok_body()),
        ]
    )
    c = PrivateLLMClient(base_url="http://fake.llm.local", api_key="", model="m")
    body = await c._post_chat({"model": "m", "messages": []}, client=fake)
    assert body["choices"]
    assert len(fake.payloads) == 2  # 同后端重试了一次


@pytest.mark.asyncio
async def test_post_chat_429_no_infinite_loop():
    """持续 429：重试上限 1 次后上抛，绝不无限循环。"""
    fake = _FakeClient(
        [
            _resp(429, text="rate limited", headers={"Retry-After": "0"}),
            _resp(429, text="rate limited", headers={"Retry-After": "0"}),
        ]
    )
    c = PrivateLLMClient(base_url="http://fake.llm.local", api_key="", model="m")
    with pytest.raises(httpx.HTTPStatusError):
        await c._post_chat({"model": "m", "messages": []}, client=fake)
    assert len(fake.payloads) == 2


@pytest.mark.asyncio
async def test_post_chat_429_retry_after_too_large_raises():
    """Retry-After 超过 3s 上限 → 不同后端傻等，直接上抛走降级链。"""
    fake = _FakeClient([_resp(429, text="rate limited", headers={"Retry-After": "60"})])
    c = PrivateLLMClient(base_url="http://fake.llm.local", api_key="", model="m")
    with pytest.raises(httpx.HTTPStatusError):
        await c._post_chat({"model": "m", "messages": []}, client=fake)
    assert len(fake.payloads) == 1


# ---- _classify_http_error：400 语义细分 ---------------------------------------


def _http_exc(status: int, server_body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", _URL)
    response = httpx.Response(status, content=server_body.encode(), request=request)
    return httpx.HTTPStatusError(
        f"Client error {status} | 服务端返回：{server_body}", request=request, response=response
    )


def test_classify_400_tool_id_mismatch():
    exc = _http_exc(400, "tool result's tool id(x) not found")
    assert _classify_http_error(exc) is LLMToolIdMismatchError


def test_classify_400_param_unsupported():
    exc = _http_exc(400, "response_format is not supported for this model")
    assert _classify_http_error(exc) is LLMParamUnsupportedError


def test_classify_400_generic_stays_backend_error():
    exc = _http_exc(400, "invalid model name")
    assert _classify_http_error(exc) is LLMBackendError


def test_classify_429_and_5xx():
    assert _classify_http_error(_http_exc(429, "too many requests")) is LLMRateLimitError
    assert _classify_http_error(_http_exc(503, "overloaded")) is LLMUnavailableError


# ---- fallback 链：429 / 请求被拒不熔断 ------------------------------------------


@pytest.mark.asyncio
async def test_with_fallback_rate_limit_does_not_trip_breaker():
    """429 切下一级但不计熔断失败 —— 限流窗口内连打 3 次不应把后端熔断。"""
    registry = CircuitBreakerRegistry(failure_threshold=3)

    async def _rate_limited():
        raise LLMRateLimitError("429")

    async def _ok():
        return "fallback-ok"

    # 同一后端连续 429 三次（超过熔断阈值）
    for _ in range(3):
        res = await with_fallback(
            chain=[("cloud", _rate_limited), ("ollama", _ok)],
            label="t159",
            circuit_breaker_registry=registry,
        )
        assert res.value == "fallback-ok"

    cb = registry.get_or_create("cloud")
    assert cb.allow() is True  # 未被熔断


@pytest.mark.asyncio
async def test_with_fallback_request_invalid_does_not_trip_breaker():
    registry = CircuitBreakerRegistry(failure_threshold=1)

    async def _param_rejected():
        raise LLMParamUnsupportedError("response_format not supported")

    async def _ok():
        return 1

    for _ in range(3):
        res = await with_fallback(
            chain=[("private", _param_rejected), ("ollama", _ok)],
            label="t159b",
            circuit_breaker_registry=registry,
        )
        assert res.value == 1
    assert registry.get_or_create("private").allow() is True


@pytest.mark.asyncio
async def test_with_fallback_unavailable_still_trips_breaker():
    """对照：真正的后端故障（5xx/超时）仍计熔断失败，语义不变。"""
    registry = CircuitBreakerRegistry(failure_threshold=2)

    async def _down():
        raise LLMUnavailableError("connect refused")

    async def _ok():
        return 2

    for _ in range(2):
        await with_fallback(
            chain=[("private", _down), ("ollama", _ok)],
            label="t159c",
            circuit_breaker_registry=registry,
        )
    assert registry.get_or_create("private").allow() is False
