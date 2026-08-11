# services/agent/tests/test_doc_review_llm.py
import pytest
from agent.config import settings
from agent.llm.fallback import LLMBackendError
from agent.llm.models import LLMBackend
from agent.llm.router import LMRouter
from agent.llm.storage import upsert_backend


def test_doc_review_chain_default():
    assert settings.doc_review_llm_chain == ["cloud", "private", "ollama"]


async def test_generate_review_mock_mode(monkeypatch):
    monkeypatch.setenv("EAIDE_LLM_BACKEND", "mock")
    router = LMRouter()
    text = await router.generate_review(kind="doc_classify", prompt="x")
    assert '"doc_category"' in text


async def test_generate_review_uses_chain_order(monkeypatch):
    # 默认链 cloud → private → ollama：未配置云端时回退 private，再回退 ollama。
    # 内网默认已移除（BUGFIX #57）：要用 private 层级需显式配置。
    # settings 是已实例化单例，改环境变量不生效 → 直接打补丁。
    monkeypatch.setattr(settings, "private_llm_base_url", "http://private.example.com/v1")
    monkeypatch.setattr(settings, "private_llm_api_key", "k")
    router = LMRouter()
    calls: list[str] = []

    async def fake_ollama_extract(messages, **kwargs):
        calls.append("ollama")
        return "ok-ollama"

    async def fake_private_extract(messages, **kwargs):
        calls.append("private")
        return "ok-private"

    monkeypatch.setattr(router.ollama, "extract_chat", fake_ollama_extract)
    monkeypatch.setattr(router.private, "extract_chat", fake_private_extract)
    # cloud 未配置（注册表为空）→ 跳过云端，直接命中 private
    text = await router.generate_review(kind="doc_analyze", prompt="x")
    assert text == "ok-private"
    assert calls == ["private"]

    # private 也不可用（连接失败）→ 回退 ollama
    calls.clear()

    async def broken_private_extract(messages, **kwargs):
        calls.append("private")
        import httpx

        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(router.private, "extract_chat", broken_private_extract)
    text = await router.generate_review(kind="doc_analyze", prompt="x")
    assert text == "ok-ollama"
    assert calls == ["private", "ollama"]


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
        def __init__(self, *, base_url, api_key, model, max_context=None):
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
