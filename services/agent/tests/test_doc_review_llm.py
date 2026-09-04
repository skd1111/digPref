# services/agent/tests/test_doc_review_llm.py
import pytest
from agent.config import settings
from agent.llm.fallback import LLMBackendError
from agent.llm.models import LLMBackend
from agent.llm.router import LMRouter
from agent.llm.storage import upsert_backend


def test_doc_review_chain_default():
    assert settings.doc_review_llm_chain == ["cloud", "private"]


async def test_generate_review_mock_mode(monkeypatch):
    monkeypatch.setenv("EAIDE_LLM_BACKEND", "mock")
    router = LMRouter()
    text = await router.generate_review(kind="doc_classify", prompt="x")
    assert '"doc_category"' in text


async def test_generate_review_uses_enabled_private_backend(monkeypatch):
    # 新语义：private 只认「模型管理」注册表里已启用的后端，不再回退 settings/env。
    # 无已启用云端时跳过 cloud，命中注册表里的 private。
    captured: list[str] = []

    class FakeClient:
        def __init__(self, *, base_url, api_key, model, max_context=None, max_output_tokens=None):
            self._base_url = base_url

        async def extract_chat(self, messages, **kwargs):
            captured.append(self._base_url)
            return f"ok:{self._base_url}"

    monkeypatch.setattr("agent.llm.router.PrivateLLMClient", FakeClient)
    await upsert_backend(
        LLMBackend(
            name="priv-1",
            type="private",
            base_url="https://private.example.com/v1",
            model_name="m",
            api_key_ref="k",
            data_residency="private",
            enabled=True,
        )
    )
    router = LMRouter()
    text = await router.generate_review(kind="doc_analyze", prompt="x")
    assert text == "ok:https://private.example.com/v1"
    assert captured == ["https://private.example.com/v1"]


async def test_generate_review_cloud_before_private(monkeypatch):
    # cloud + private 都已启用 → 云端优先，内网不被调用（顺序 cloud → private）。
    order: list[str] = []

    class FakeClient:
        def __init__(self, *, base_url, api_key, model, max_context=None, max_output_tokens=None):
            self._base_url = base_url

        async def extract_chat(self, messages, **kwargs):
            order.append(self._base_url)
            return f"ok:{self._base_url}"

    monkeypatch.setattr("agent.llm.router.PrivateLLMClient", FakeClient)
    await upsert_backend(
        LLMBackend(
            name="cloud-1",
            type="cloud",
            base_url="https://cloud.example.com/v1",
            model_name="gpt-4o",
            api_key_ref="k",
            data_residency="cloud",
            enabled=True,
        )
    )
    await upsert_backend(
        LLMBackend(
            name="priv-1",
            type="private",
            base_url="https://private.example.com/v1",
            model_name="m",
            api_key_ref="k",
            data_residency="private",
            enabled=True,
        )
    )
    router = LMRouter()
    text = await router.generate_review(kind="doc_analyze", prompt="x")
    assert text == "ok:https://cloud.example.com/v1"
    assert order == ["https://cloud.example.com/v1"]


async def test_generate_review_no_ollama_fallback_when_none_enabled(monkeypatch):
    # 默认链 cloud → private：两者都未启用（注册表空）→ 不再回退本地 ollama，直接抛错。
    monkeypatch.setattr(settings, "doc_review_llm_chain", ["cloud", "private"])
    router = LMRouter()
    with pytest.raises(LLMBackendError, match="generate_review failed"):
        await router.generate_review(kind="doc_analyze", prompt="x")


async def test_generate_review_uses_enabled_cloud_backend(monkeypatch):
    monkeypatch.setattr(settings, "doc_review_llm_chain", ["cloud"])
    await upsert_backend(
        LLMBackend(
            name="cloud-1",
            type="cloud",
            base_url="https://cloud.example.com/v1",
            model_name="gpt-4o",
            api_key_ref="k",
            data_residency="cloud",
            enabled=True,
        )
    )
    captured = {}

    class FakeCloud:
        def __init__(self, *, base_url, api_key, model, max_context=None, max_output_tokens=None):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["model"] = model

        async def extract_chat(self, messages, **kwargs):
            captured["messages"] = messages
            return "cloud-ok"

    monkeypatch.setattr("agent.llm.router.PrivateLLMClient", FakeCloud)
    router = LMRouter()
    text = await router.generate_review(kind="doc_analyze", prompt="x")
    assert text == "cloud-ok"
    assert captured["base_url"] == "https://cloud.example.com/v1"
    assert captured["api_key"] == "k"
    assert captured["model"] == "gpt-4o"
    # prompt 透传：extract_chat 收到的是原始审核提示词（不包汇总模板）
    assert captured["messages"] == [{"role": "user", "content": "x"}]


async def test_generate_review_skips_cloud_when_none_enabled(monkeypatch):
    monkeypatch.setattr(settings, "doc_review_llm_chain", ["cloud"])
    router = LMRouter()
    with pytest.raises(LLMBackendError, match="cloud"):
        await router.generate_review(kind="doc_analyze", prompt="x")
