"""LLMBackend 模型协议校验测试（Phase 2C V0 用户约定）。"""
import pytest

from agent.llm.models import LLMBackend, RESIDENCY_LOCAL, RESIDENCY_PRIVATE


def _local() -> LLMBackend:
    return LLMBackend(
        name="ollama", type="local", base_url="http://127.0.0.1:11434",
        model_name="qwen2.5:0.5b", data_residency=RESIDENCY_LOCAL,
    )


def _private() -> LLMBackend:
    return LLMBackend(
        name="deepseek-internal", type="private", base_url="http://internal-deepseek.lan/v1",
        model_name="deepseek-r1", data_residency=RESIDENCY_PRIVATE,
    )


def _cloud() -> LLMBackend:
    return LLMBackend(
        name="gpt4o", type="cloud", base_url="https://api.openai.com/v1",
        model_name="gpt-4o", api_key_ref="llm.openai.api_key",
        data_residency="cloud",
    )


def test_local_ollama_no_apikey_required():
    """端侧 Ollama 不需要 api_key_ref。"""
    assert _local().validate_protocol() is None


def test_local_ollama_base_url_required():
    bad = LLMBackend(name="x", type="local", base_url="", model_name="m",
                      data_residency=RESIDENCY_LOCAL)
    err = bad.validate_protocol()
    assert err is not None
    assert "base_url" in err


def test_private_no_apikey_required():
    """内网（private）OpenAI 格式：只需要 base_url，不需要 api_key。"""
    assert _private().validate_protocol() is None


def test_private_base_url_required():
    bad = LLMBackend(name="x", type="private", base_url="", model_name="m",
                      data_residency=RESIDENCY_PRIVATE)
    err = bad.validate_protocol()
    assert err is not None
    assert "base_url" in err


def test_cloud_requires_apikey_ref():
    """云端必须 api_key_ref（Keyring 占位符，禁明文）。"""
    bad = LLMBackend(
        name="gpt4o", type="cloud", base_url="https://api.openai.com/v1",
        model_name="gpt-4o", data_residency="cloud",  # 没 api_key_ref
    )
    err = bad.validate_protocol()
    assert err is not None
    assert "api_key_ref" in err


def test_cloud_valid_with_apikey_ref():
    """云端 + base_url + api_key_ref 全部 OK。"""
    assert _cloud().validate_protocol() is None


def test_unknown_type_blocked():
    bad = LLMBackend(name="x", type="unknown", base_url="x", model_name="m",
                      data_residency=RESIDENCY_LOCAL)
    err = bad.validate_protocol()
    assert err is not None
    assert "未知" in err
