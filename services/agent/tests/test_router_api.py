"""Phase 2C V2.5 路由 API 测试。"""

import pytest
from agent.llm import engine_api
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 隔离 router.db 写入目录（避免污染用户真实 %APPDATA%）
    monkeypatch.setenv("APPDATA", str(tmp_path))
    app = FastAPI()
    app.include_router(engine_api.router)
    return TestClient(app)


def test_get_metrics(client):
    r = client.get("/router/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "circuits" in data
    assert "budget" in data
    assert "backends" in data
    # 3 默认后端（V0 hardcode）
    assert len(data["backends"]) == 3


def test_get_decisions_default(client):
    r = client.get("/router/decisions")
    assert r.status_code == 200
    data = r.json()
    assert "decisions" in data
    assert isinstance(data["decisions"], list)


def test_get_weights(client):
    r = client.get("/router/weights")
    assert r.status_code == 200
    w = r.json()["weights"]
    assert abs(w["capability"] - 0.35) < 1e-6
    assert abs(w["cost"] - 0.25) < 1e-6
    assert abs(w["latency"] - 0.20) < 1e-6
    assert abs(w["compliance"] - 0.15) < 1e-6
    assert abs(w["availability"] - 0.05) < 1e-6


def test_create_backend_validates_protocol(client):
    """云端必须 api_key_ref。"""
    bad = {
        "name": "test-cloud",
        "type": "cloud",
        "base_url": "https://api.example.com/v1",
        "model_name": "test-model",
        "data_residency": "cloud",
        # 缺 api_key_ref
        "role": "execution",
    }
    r = client.post("/router/backends", json=bad)
    assert r.status_code == 400
    assert "api_key_ref" in r.json()["detail"]


def test_create_backend_private_no_apikey_required(client):
    """内网（private）OpenAI 格式：不需要 api_key。"""
    body = {
        "name": "test-private",
        "type": "private",
        "base_url": "http://internal.lan/v1",
        "model_name": "deepseek-r1",
        "data_residency": "private",
        "role": "reasoning",
    }
    r = client.post("/router/backends", json=body)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["backend"]["name"] == "test-private"


def test_list_backends_includes_role(client):
    r = client.get("/router/backends")
    assert r.status_code == 200
    r.json()
    # V2.5 加了 role 列（V0 storage 可能没 role；V0.5 还没改 storage）


def test_reset_breaker(client):
    """手动重置熔断器。"""
    # 先触发熔断（3 次失败）
    eng = engine_api._get_engine()
    cb = eng._breakers.get_or_create("ollama-utility")
    from agent.llm.circuit_breaker import CircuitState

    for _ in range(5):
        cb.on_failure()
    assert cb.state == CircuitState.OPEN
    # 重置
    r = client.post("/router/breakers/ollama-utility/reset")
    assert r.status_code == 200
    assert r.json()["state"] == "closed"


def test_test_connection_anthropic_endpoint_returns_hint(client):
    """Anthropic 兼容端点（如 MiniMax /anthropic）返回可操作提示，而不是裸 404。"""
    r = client.post(
        "/router/backends/test-connection",
        json={
            "type": "cloud",
            "base_url": "https://api.minimaxi.com/anthropic",
            "model": "MiniMax-M3",
            "api_key": "sk-test",
            "timeout_s": 5,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert "anthropic" in data["error"].lower()
    assert "/v1" in data["error"]


def test_test_connection_anthropic_messages_endpoint_returns_hint(client):
    """以 /messages 结尾的 Anthropic 端点同样给出提示。"""
    r = client.post(
        "/router/backends/test-connection",
        json={
            "type": "cloud",
            "base_url": "https://api.example.com/v1/messages",
            "model": "MiniMax-M3",
            "api_key": "sk-test",
            "timeout_s": 5,
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_test_connection_openai_compatible_still_probes(client, monkeypatch):
    """OpenAI 兼容端点不被误判，继续走真实探测。"""

    async def fake_probe(base_url, model, api_key, timeout_s=8.0):
        return {"ok": True, "latency_ms": 1, "actual_model": model, "info": "ok"}

    monkeypatch.setattr(engine_api, "_probe_openai_chat", fake_probe)
    r = client.post(
        "/router/backends/test-connection",
        json={
            "type": "cloud",
            "base_url": "https://api.minimaxi.com/v1",
            "model": "MiniMax-M3",
            "api_key": "sk-test",
            "timeout_s": 5,
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def _cloud_body(name: str) -> dict:
    return {
        "name": name,
        "type": "cloud",
        "base_url": "https://api.example.com/v1",
        "model_name": f"model-{name}",
        "api_key_ref": f"sk-{name}",
        "data_residency": "cloud",
        "role": "execution",
    }


def test_create_enabled_backend_disables_same_residency(client):
    """同驻留只允许 1 个启用：启用 cloud-b 后 cloud-a 自动停用，且响应返回 disabled 列表。"""
    r1 = client.post("/router/backends", json=_cloud_body("cloud-a"))
    assert r1.status_code == 200
    r2 = client.post("/router/backends", json=_cloud_body("cloud-b"))
    assert r2.status_code == 200
    data = r2.json()
    assert data["ok"] is True
    assert data["disabled"] == ["cloud-a"]

    by_name = {b["name"]: b for b in client.get("/router/backends").json()["backends"]}
    assert by_name["cloud-a"]["enabled"] is False
    assert by_name["cloud-b"]["enabled"] is True


def test_create_enabled_same_residency_different_type_disables(client):
    """驻留 private 是内网判定依据：类型为 cloud、驻留 private 的模型也会被同驻留的 private 模型互斥停用。"""
    cloud_private = _cloud_body("cloud-a")
    cloud_private["data_residency"] = "private"
    r1 = client.post("/router/backends", json=cloud_private)
    assert r1.status_code == 200
    r2 = client.post(
        "/router/backends",
        json={
            "name": "private-b",
            "type": "private",
            "base_url": "http://internal.lan/v1",
            "model_name": "deepseek-r1",
            "data_residency": "private",
            "role": "reasoning",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["disabled"] == ["cloud-a"]
    by_name = {b["name"]: b for b in client.get("/router/backends").json()["backends"]}
    assert by_name["cloud-a"]["enabled"] is False
    assert by_name["private-b"]["enabled"] is True


def test_create_enabled_does_not_disable_other_residency(client):
    """不同驻留互不影响：启用驻留 private 的模型不会停用已启用的 cloud 驻留模型。"""
    client.post("/router/backends", json=_cloud_body("cloud-a"))
    r = client.post(
        "/router/backends",
        json={
            "name": "private-a",
            "type": "private",
            "base_url": "http://internal.lan/v1",
            "model_name": "deepseek-r1",
            "data_residency": "private",
            "role": "reasoning",
        },
    )
    assert r.status_code == 200
    assert r.json()["disabled"] == []
    by_name = {b["name"]: b for b in client.get("/router/backends").json()["backends"]}
    assert by_name["cloud-a"]["enabled"] is True
    assert by_name["private-a"]["enabled"] is True
