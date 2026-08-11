# services/agent/tests/test_llm_active_config.py
"""LLM active 后端统一配置（双轨制统一）单元测试。"""

from __future__ import annotations

import json

import pytest
from agent.llm import active_config as ac


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """db 与配置目录都隔离到 tmp_path，并清掉外部 env 干扰。"""
    monkeypatch.delenv("EAIDE_LLM_BACKEND", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("EAIDE_LLM_ROUTER_DB_PATH", str(tmp_path / "router.db"))
    monkeypatch.setenv("EAIDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    # apply_active 直接写 os.environ，退场时清理防跨用例泄漏
    import os as _os

    for k in (
        "EAIDE_LLM_BACKEND",
        "EAIDE_OLLAMA_BASE_URL",
        "EAIDE_OLLAMA_MODEL",
        "EAIDE_PRIVATE_LLM_BASE_URL",
        "EAIDE_PRIVATE_LLM_API_KEY",
        "EAIDE_PRIVATE_LLM_MODEL",
    ):
        _os.environ.pop(k, None)


def test_default_is_ollama_not_mock(isolated):
    cfg = ac.resolve_active()
    assert cfg["active"] == "ollama"


def test_env_override_wins(isolated, monkeypatch):
    monkeypatch.setenv("EAIDE_LLM_BACKEND", "mock")
    assert ac.resolve_active()["active"] == "mock"


def test_db_kv_is_used(isolated):
    ac._kv_write({"active": "private", "private": {"base_url": "http://x/v1"}})
    cfg = ac.resolve_active()
    assert cfg["active"] == "private"
    assert cfg["private"]["base_url"] == "http://x/v1"


def test_legacy_json_read_and_migrated_when_consuming(isolated):
    legacy_path = ac._legacy_json_path()
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps({"active": "custom", "custom": {"model": "m1"}}))
    cfg = ac.resolve_active(force_consume=True)
    assert cfg["active"] == "custom"
    # 邮箱被消费：文件删除 + 迁入 db
    assert not legacy_path.exists()
    assert ac._kv_read()["active"] == "custom"


def test_legacy_json_readonly_under_pytest(isolated, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "fake")
    legacy_path = ac._legacy_json_path()
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps({"active": "ollama"}))
    assert ac.resolve_active()["active"] == "ollama"
    assert legacy_path.exists()  # 未被删除


def test_save_active_persists_and_mirrors(isolated):
    ac.save_active({"active": "ollama", "ollama": {"model": "qwen2.5:7b"}})
    assert ac._kv_read()["ollama"]["model"] == "qwen2.5:7b"
    assert ac._legacy_json_path().exists()
    assert ac.load_saved_active()["active"] == "ollama"


def test_apply_active_sets_env(isolated, monkeypatch):
    monkeypatch.delenv("EAIDE_OLLAMA_MODEL", raising=False)
    ac.apply_active({"active": "ollama", "ollama": {"base_url": "http://o:1", "model": "m9"}})
    import os

    assert os.environ["EAIDE_LLM_BACKEND"] == "ollama"
    assert os.environ["EAIDE_OLLAMA_BASE_URL"] == "http://o:1"
    assert os.environ["EAIDE_OLLAMA_MODEL"] == "m9"


def test_apply_active_invalid_falls_back_ollama(isolated, monkeypatch):
    # 避免上一个用例的 save_active 镜像 json 干扰（这里只关心非法 active 兜底）
    ac._legacy_json_path().unlink(missing_ok=True)
    cfg = ac.apply_active({"active": "nonsense"})
    import os

    assert cfg["active"] == "ollama"
    assert os.environ["EAIDE_LLM_BACKEND"] == "ollama"
