"""test_codenav_backend_binding.py —— Phase 2F 代码导航 backend 绑定测试。"""

from __future__ import annotations

import pytest


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """每次用临时 cwd 隔离 router.db。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_feature_backend_get_returns_none_when_unbound(isolated_cwd):
    from agent.llm.storage import get_feature_backend

    assert await get_feature_backend("codenav") is None


@pytest.mark.asyncio
async def test_feature_backend_set_then_get(isolated_cwd):
    from agent.llm.storage import get_feature_backend, set_feature_backend

    await set_feature_backend("codenav", "deepseek-cloud")
    assert await get_feature_backend("codenav") == "deepseek-cloud"


@pytest.mark.asyncio
async def test_feature_backend_unbind_with_none(isolated_cwd):
    from agent.llm.storage import get_feature_backend, set_feature_backend

    await set_feature_backend("codenav", "deepseek-cloud")
    await set_feature_backend("codenav", None)
    assert await get_feature_backend("codenav") is None


@pytest.mark.asyncio
async def test_feature_backend_unbind_with_empty_string(isolated_cwd):
    from agent.llm.storage import get_feature_backend, set_feature_backend

    await set_feature_backend("codenav", "deepseek-cloud")
    await set_feature_backend("codenav", "")
    assert await get_feature_backend("codenav") is None


@pytest.mark.asyncio
async def test_feature_backend_upsert_overwrites(isolated_cwd):
    from agent.llm.storage import get_feature_backend, set_feature_backend

    await set_feature_backend("codenav", "a")
    await set_feature_backend("codenav", "b")
    assert await get_feature_backend("codenav") == "b"


@pytest.mark.asyncio
async def test_resolve_returns_none_without_anything(isolated_cwd, monkeypatch):
    monkeypatch.delenv("EAIDE_CODENAV_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("EAIDE_CODENAV_LLM_MODEL", raising=False)
    monkeypatch.delenv("EAIDE_CODENAV_LLM_API_KEY", raising=False)
    from agent.codenav.llm_client import resolve_codenav_backend

    assert await resolve_codenav_backend() is None


@pytest.mark.asyncio
async def test_resolve_env_fallback(isolated_cwd, monkeypatch):
    monkeypatch.setenv("EAIDE_CODENAV_LLM_BASE_URL", "http://env-host/v1")
    monkeypatch.setenv("EAIDE_CODENAV_LLM_MODEL", "env-model")
    monkeypatch.setenv("EAIDE_CODENAV_LLM_API_KEY", "env-key")
    from agent.codenav.llm_client import resolve_codenav_backend

    cfg = await resolve_codenav_backend()
    assert cfg is not None
    assert cfg["base_url"] == "http://env-host/v1"
    assert cfg["model"] == "env-model"
    assert cfg["api_key"] == "env-key"
    assert cfg["name"] == "env"


@pytest.mark.asyncio
async def test_resolve_bound_to_missing_falls_back_to_env(isolated_cwd, monkeypatch):
    """bound 到不存在的 backend → resolve 降级到 env（便于离线开发）。"""
    monkeypatch.setenv("EAIDE_CODENAV_LLM_BASE_URL", "http://env/v1")
    monkeypatch.setenv("EAIDE_CODENAV_LLM_MODEL", "env-model")
    from agent.codenav.llm_client import resolve_codenav_backend
    from agent.llm.storage import set_feature_backend

    await set_feature_backend("codenav", "nonexistent")
    cfg = await resolve_codenav_backend()
    # bound 找不到 → env 兜底
    assert cfg is not None
    assert cfg["name"] == "env"


@pytest.mark.asyncio
async def test_resolve_with_bound_backend_works(isolated_cwd):
    """先在 llm_backends 插一个，再绑 codenav → resolve 应返回这个 backend。"""
    from agent.codenav.llm_client import resolve_codenav_backend
    from agent.llm.models import LLMBackend
    from agent.llm.storage import set_feature_backend
    from agent.llm.storage import upsert_backend as insert_backend

    backend = LLMBackend(
        name="cloud-test",
        type="cloud",
        base_url="https://api.example.com/v1",
        model_name="gpt-4o-mini",
        api_key_ref=None,  # key 不在 keyring → api_key="" → 仍 configured（有 base_url + model）
    )
    await insert_backend(backend)
    await set_feature_backend("codenav", "cloud-test")

    cfg = await resolve_codenav_backend()
    assert cfg is not None
    assert cfg["name"] == "cloud-test"
    assert cfg["base_url"] == "https://api.example.com/v1"
    assert cfg["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_resolve_preferred_overrides_bound(isolated_cwd):
    """preferred_name 直接覆盖 bound。"""
    from agent.codenav.llm_client import resolve_codenav_backend
    from agent.llm.models import LLMBackend
    from agent.llm.storage import set_feature_backend
    from agent.llm.storage import upsert_backend as insert_backend

    a = LLMBackend(name="a", type="local", base_url="http://a/v1", model_name="m-a")
    b = LLMBackend(name="b", type="local", base_url="http://b/v1", model_name="m-b")
    await insert_backend(a)
    await insert_backend(b)
    await set_feature_backend("codenav", "a")

    cfg = await resolve_codenav_backend(preferred_name="b")
    assert cfg["name"] == "b"


# ---------------------------------------------------------------------------
# /codenav/llm-backend + /bind 端点
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_backend_endpoint_returns_unbound(isolated_cwd, monkeypatch):
    monkeypatch.delenv("EAIDE_CODENAV_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("EAIDE_CODENAV_LLM_MODEL", raising=False)
    from fastapi.testclient import TestClient

    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TestClient(app)
    resp = c.get("/codenav/llm-backend")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bound"] is None
    assert body["resolved"] is None
    assert body["candidates"] == []  # llm_backends 空


@pytest.mark.asyncio
async def test_llm_backend_bind_endpoint(isolated_cwd, monkeypatch):
    from agent.llm.models import LLMBackend
    from agent.llm.storage import upsert_backend as insert_backend

    monkeypatch.delenv("EAIDE_CODENAV_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("EAIDE_CODENAV_LLM_MODEL", raising=False)
    backend = LLMBackend(
        name="my-cloud",
        type="cloud",
        base_url="https://api.test/v1",
        model_name="test-model",
    )
    await insert_backend(backend)

    from fastapi.testclient import TestClient

    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TestClient(app)
    # 绑定
    resp = c.post("/codenav/llm-backend/bind", json={"backend_name": "my-cloud"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["bound"] == "my-cloud"
    assert body["resolved"] is not None
    assert body["resolved"]["base_url"] == "https://api.test/v1"
    assert body["resolved"]["source"] == "router_db_bound"
    # 解绑
    resp2 = c.post("/codenav/llm-backend/bind", json={"backend_name": None})
    assert resp2.status_code == 200
    assert resp2.json()["bound"] is None
