"""test_codenav_api.py —— FastAPI 路由 + mcp_tools 测试。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env_isolated(tmp_path, monkeypatch):
    """隔离 APPDATA → tmp_path，让 codenav 写到 tmp_path 而非真实 APPDATA。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("EAIDE_WORKSPACE_INDEX_DB", str(tmp_path / "idx.db"))
    monkeypatch.setenv("EAIDE_CODE_NAV_ROOTS", str(tmp_path / "src"))
    (tmp_path / "src").mkdir()
    return tmp_path


@pytest.fixture
def client(env_isolated):
    # 测试时直接重新 import api 模块以触发单例初始化
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    # 准备测试文件
    (env_isolated / "src" / "A.java").write_text(
        "public class HelloWorld { void main() {} }",
        encoding="utf-8",
    )
    return codenav_api


def test_jump_local_hit(client):
    """本地索引命中 → source='local_index', confidence=1.0"""
    import asyncio
    asyncio.run(client._get_indexer().full_scan())

    from fastapi.testclient import TestClient as TC
    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TC(app)
    resp = c.post("/codenav/jump", json={
        "symbol": "HelloWorld",
        "current_file": str(client._get_indexer()._db_path),
        "context": "",
        "line": 0,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "local_index"
    assert body["confidence"] == 1.0
    assert body["line"] >= 1


def test_jump_not_found(client):
    """V1：未命中 → source='not_found'，不跳不调 LLM（避免幻觉）"""
    import asyncio
    asyncio.run(client._get_indexer().full_scan())

    from fastapi.testclient import TestClient as TC
    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TC(app)
    resp = c.post("/codenav/jump", json={
        "symbol": "NonExistent",
        "current_file": str(client._get_indexer()._db_path),
        "context": "ctx",
        "line": 0,
    })
    assert resp.status_code == 200
    body = resp.json()
    # V1：纯索引，未命中就 not_found
    assert body["source"] == "not_found"
    assert body["confidence"] == 0.0
    assert body["file_path"] == "" or body["file_path"] == "NonExistent"  # 不应跳


def test_index_and_status(client):
    import asyncio
    from fastapi.testclient import TestClient as TC
    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TC(app)
    # 先索引
    resp = c.post("/codenav/index", json={"root_paths": None})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_scanning"] is False

    # status
    resp2 = c.get("/codenav/status")
    assert resp2.status_code == 200
    status = resp2.json()
    assert status["total_files"] >= 1
    assert status["total_symbols"] >= 2


def test_list_symbols(client):
    import asyncio
    asyncio.run(client._get_indexer().full_scan())

    from fastapi.testclient import TestClient as TC
    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TC(app)
    resp = c.get("/codenav/symbols?name=HelloWorld")
    assert resp.status_code == 200
    list_ = resp.json()
    assert isinstance(list_, list)
    assert any(s["name"] == "HelloWorld" for s in list_)
