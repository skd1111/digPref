# services/agent/tests/test_doc_review_llm.py
import pytest

from agent.config import settings
from agent.llm.fallback import LLMBackendError
from agent.llm.models import LLMBackend
from agent.llm.router import LMRouter
from agent.llm.storage import upsert_backend


def test_doc_review_chain_default():
    assert settings.doc_review_llm_chain == ["ollama", "private", "cloud"]


async def test_generate_review_mock_mode(monkeypatch):
    monkeypatch.setenv("EAIDE_LLM_BACKEND", "mock")
    router = LMRouter()
    text = await router.generate_review(kind="doc_classify", prompt="x")
    assert '"doc_category"' in text


async def test_generate_review_uses_chain_order(monkeypatch):
    router = LMRouter()
    calls: list[str] = []

    async def fake_ollama_summarise(**kwargs):
        calls.append("ollama")
        return "ok-ollama", []

    async def fake_private_summarise(**kwargs):
        calls.append("private")
        return "ok-private", []

    monkeypatch.setattr(router.ollama, "summarise", fake_ollama_summarise)
    monkeypatch.setattr(router.private, "summarise", fake_private_summarise)
    text = await router.generate_review(kind="doc_analyze", prompt="x")
    assert text == "ok-ollama"
    assert calls == ["ollama"]


async def test_generate_review_uses_enabled_cloud_backend(monkeypatch):
    monkeypatch.setattr(settings, "doc_review_llm_chain", ["cloud"])
    await upsert_backend(LLMBackend(
        name="cloud-1", type="cloud", base_url="https://cloud.example.com/v1",
        model_name="gpt-4o", api_key_ref="k", data_residency="cloud", enabled=True,
    ))
    captured = {}

    class FakeCloud:
        def __init__(self, *, base_url, api_key, model, max_context=None):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["model"] = model

        async def summarise(self, **kwargs):
            return "cloud-ok", []

    monkeypatch.setattr("agent.llm.router.PrivateLLMClient", FakeCloud)
    router = LMRouter()
    text = await router.generate_review(kind="doc_analyze", prompt="x")
    assert text == "cloud-ok"
    assert captured["base_url"] == "https://cloud.example.com/v1"
    assert captured["api_key"] == "k"
    assert captured["model"] == "gpt-4o"


async def test_generate_review_skips_cloud_when_none_enabled(monkeypatch):
    monkeypatch.setattr(settings, "doc_review_llm_chain", ["cloud"])
    router = LMRouter()
    with pytest.raises(LLMBackendError, match="cloud"):
        await router.generate_review(kind="doc_analyze", prompt="x")
