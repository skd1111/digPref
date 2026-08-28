"""MCP 配置管理端点：/mcp-config 读写往返 + 校验红线 + 连通性测试降级。"""

from __future__ import annotations

import pytest
import yaml
from agent.api.mcp_config import router as mcp_config_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "mcp_config_path", str(tmp_path / "mcp.yaml"))
    app = FastAPI()
    app.include_router(mcp_config_router)
    return TestClient(app)


def _entry(**over):
    base = {"command": "uvx", "args": ["duckduckgo-mcp-server"], "env": {}}
    base.update(over)
    return base


def test_get_missing_file(client):
    resp = client.get("/mcp-config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["servers"] == {}


def test_save_and_roundtrip(client, tmp_path):
    resp = client.put(
        "/mcp-config",
        json={
            "servers": {
                "websearch": _entry(),
                "brave": _entry(
                    command="npx",
                    args=["-y", "@modelcontextprotocol/server-brave-search"],
                    env={"BRAVE_API_KEY": "__KEYRING_REF:mcp.brave.api_key__"},
                ),
            }
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # 落盘内容可被 yaml 解析且结构正确
    data = yaml.safe_load((tmp_path / "mcp.yaml").read_text(encoding="utf-8"))
    assert set(data["servers"].keys()) == {"websearch", "brave"}
    assert data["servers"]["brave"]["env"]["BRAVE_API_KEY"].startswith("__KEYRING_REF:")

    again = client.get("/mcp-config")
    assert again.json()["exists"] is True
    assert again.json()["servers"]["websearch"]["command"] == "uvx"


@pytest.mark.parametrize(
    "name",
    ["bad name", "中文", "a/b", "x" * 65, ""],
)
def test_reject_bad_server_name(client, name):
    resp = client.put("/mcp-config", json={"servers": {name: _entry()}})
    assert resp.status_code == 422


def test_reject_empty_command(client):
    resp = client.put(
        "/mcp-config", json={"servers": {"x": {"command": "", "args": [], "env": {}}}}
    )
    assert resp.status_code == 422


def test_reject_plaintext_secret_in_env(client):
    """凭证红线：疑似密钥键 + 非占位符值 → 硬拒。"""
    resp = client.put(
        "/mcp-config",
        json={"servers": {"x": _entry(env={"SOME_API_KEY": "sk-abcdef123456"})}},
    )
    assert resp.status_code == 422


def test_keyring_ref_placeholder_allowed(client):
    resp = client.put(
        "/mcp-config",
        json={"servers": {"x": _entry(env={"SOME_API_KEY": "__KEYRING_REF:mcp.x.key__"})}},
    )
    assert resp.status_code == 200


def test_test_endpoint_reports_missing_command(client):
    resp = client.post(
        "/mcp-config/test", json={"name": "x", "command": "no-such-cmd-xyz", "args": []}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]
