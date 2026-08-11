"""Phase 18 工具链配置端点：GET/POST /toolchain 往返 + 键白名单过滤。"""

from __future__ import annotations

import pytest
from agent.api.toolchain import router as toolchain_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "toolchain_config_path", str(tmp_path / "toolchain.json"))
    app = FastAPI()
    app.include_router(toolchain_router)
    return TestClient(app)


def test_get_empty(client):
    resp = client.get("/toolchain")
    assert resp.status_code == 200
    assert resp.json() == {"paths": {}}


def test_save_and_reload_roundtrip(client):
    resp = client.post(
        "/toolchain",
        json={"paths": {"python": "D:/py/python.exe", "pnpm": "D:/pnpm/pnpm.exe"}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    again = client.get("/toolchain")
    assert again.json()["paths"] == {
        "python": "D:/py/python.exe",
        "pnpm": "D:/pnpm/pnpm.exe",
    }


def test_save_filters_unknown_keys_and_empty_values(client):
    resp = client.post(
        "/toolchain",
        json={"paths": {"python": "D:/py/python.exe", "hacker_tool": "/x", "node": "  "}},
    )
    assert resp.status_code == 200
    assert resp.json()["paths"] == {"python": "D:/py/python.exe"}
