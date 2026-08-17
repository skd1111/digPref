"""chat 会话模型 override 测试（2026-08-17）。

输入框模型选择器 → ChatRequest.modelOverride → LMRouter.set_chat_model_override
→ summarise 降级链置顶（优先级最高）；失败仍降级默认链；未选回落模型管理配置。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent.llm.router import LMRouter


class _FakeBackend:
    """summarise 桩客户端：记录调用、可配置抛错。"""

    def __init__(self, answer: str = "override-answer", fail: bool = False):
        self.answer = answer
        self.fail = fail
        self.calls = 0
        self.base_url = "http://fake.test"

    async def summarise(self, *, intent, user_prompt, plan, results):
        self.calls += 1
        if self.fail:
            raise RuntimeError("override backend down")
        return self.answer, []


@pytest.fixture
def router(monkeypatch):
    r = LMRouter()
    # 默认链全部禁用，只留被测的 override / 显式注入的候选
    r.private = None
    monkeypatch.setattr(r, "_build_cloud_client", _async_none)
    return r


async def _async_none():
    return None


class TestSetOverride:
    def test_set_and_clean(self, router):
        router.set_chat_model_override(" m1 ")
        assert router.chat_model_override == "m1"
        router.set_chat_model_override("")
        assert router.chat_model_override is None
        router.set_chat_model_override(None)
        assert router.chat_model_override is None


class TestBuildOverrideClient:
    async def test_no_override_returns_none(self, router):
        assert await router._build_override_client() is None

    async def test_matches_enabled_backend(self, router, monkeypatch):
        router.set_chat_model_override("rd-llama")
        backends = [
            SimpleNamespace(
                name="rd-llama",
                type="cloud",
                base_url="http://172.1.0.134:8000/v1/",
                model_name="DeepSeek-RD-Llama-70B-Int8",
                api_key_ref="",
                max_context=None,
            )
        ]

        async def fake_list(enabled_only=True):
            return backends

        import agent.llm.storage as storage

        monkeypatch.setattr(storage, "list_backends", fake_list)
        client = await router._build_override_client()
        assert client is not None
        assert client.model == "DeepSeek-RD-Llama-70B-Int8"
        # base_url 尾部斜杠被规整
        assert client.base_url.endswith("/v1")

    async def test_disabled_or_missing_returns_none(self, router, monkeypatch):
        router.set_chat_model_override("ghost")

        async def fake_list(enabled_only=True):
            return []

        import agent.llm.storage as storage

        monkeypatch.setattr(storage, "list_backends", fake_list)
        assert await router._build_override_client() is None


class TestSummariseChain:
    async def test_override_highest_priority(self, router, monkeypatch):
        fake = _FakeBackend()
        monkeypatch.setattr(router, "_build_override_client", _make_builder(fake))
        router.set_chat_model_override("rd-llama")
        answer, sources = await router.summarise(
            intent="query", user_prompt="优先级测试 A", plan=[], results=[]
        )
        assert answer == "override-answer"
        assert sources == []
        assert fake.calls == 1

    async def test_override_failure_falls_back(self, router, monkeypatch):
        bad = _FakeBackend(fail=True)
        good = _FakeBackend(answer="fallback-answer")
        monkeypatch.setattr(router, "_build_override_client", _make_builder(bad))
        router.private = good  # 默认链候选之一
        router.set_chat_model_override("rd-llama")
        answer, _ = await router.summarise(
            intent="query", user_prompt="降级测试 B", plan=[], results=[]
        )
        assert answer == "fallback-answer"
        assert bad.calls == 1 and good.calls == 1

    async def test_no_override_keeps_default_chain(self, router, monkeypatch):
        good = _FakeBackend(answer="default-answer")
        router.private = good
        # 未设置 override
        answer, _ = await router.summarise(
            intent="query", user_prompt="默认链测试 C", plan=[], results=[]
        )
        assert answer == "default-answer"


def _make_builder(client):
    async def _builder():
        return client

    return _builder


class TestChatRequestAlias:
    def test_model_override_alias(self):
        from agent.api.chat import ChatRequest

        body = ChatRequest.model_validate({"prompt": "hi", "modelOverride": "rd-llama"})
        assert body.model_override == "rd-llama"
        # 缺省 = None（回落模型管理配置）
        assert ChatRequest.model_validate({"prompt": "hi"}).model_override is None
