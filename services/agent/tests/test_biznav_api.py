"""test_biznav_api.py —— FastAPI 路由测试（Phase 2G V1.1）。

策略：把 biznav_api.router 临时挂到一个 fresh FastAPI 子 app 上，
避免触发 agent.main.create_app() 的 lifespan + 整个 MCP / LLM 启动序列。
所有调用走 TestClient（sync）。

测试矩阵：
- test_extract_returns_job_id          （POST /biznav/extract 起异步任务）
- test_list_features_filter_by_category
- test_update_feature_validates_version
- test_delete_feature
- test_export_yaml + import_yaml
- test_status_endpoint_empty
- test_affected_returns_empty_v11
- test_reload_returns_503_v11
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def biznav_app(tmp_path, monkeypatch):
    """挂载 biznav_api.router 到 fresh FastAPI 子 app，所有 storage 走 tmp_path。"""
    db = str(tmp_path / "biznav.db")
    audit_db = str(tmp_path / "audit.sqlite")
    monkeypatch.setenv("EAIDE_BIZNAV_DB", db)
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", audit_db)

    # 重置 storage 单例
    from agent.biznav import api as biznav_api
    biznav_api._reset_storage_for_tests()

    app = FastAPI()
    app.include_router(biznav_api.router)
    return app, db


@pytest.fixture
def client(biznav_app):
    app, db = biznav_app
    return TestClient(app), db


# ---------------------------------------------------------------------------
# 工具：构造 feature
# ---------------------------------------------------------------------------


def _seed_feature(storage, feature_id: str = "a", **kw):
    from agent.biznav.models import Feature, RelatedFile

    base = dict(
        id=feature_id,
        name="订单管理",
        description="订单 CRUD",
        category="业务",
        project_name="demo",
        project_root="/tmp/demo",
        related_files=[RelatedFile(path="src/order/X.java")],
        source="ai",
        version=1,
    )
    base.update(kw)
    storage.upsert(Feature(**base))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_extract_returns_job_id(client, tmp_path):
    c, db = client
    # 准备一个项目根目录
    root = tmp_path / "proj"
    root.mkdir()
    (root / "x.java").write_text("class X {}", encoding="utf-8")
    resp = c.post(
        "/biznav/extract",
        json={"project_name": "demo", "project_root": str(root)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert body["project_name"] == "demo"
    assert body["status"] == "pending"
    # job 应该已经写入 extraction_jobs
    import sqlite3
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT id, status FROM extraction_jobs").fetchall()
    assert len(rows) == 1
    assert rows[0][1] in ("pending", "scanning", "extracting", "done", "failed")


def test_list_features_filter_by_category(client):
    c, db = client
    from agent.biznav import api as biznav_api
    storage = biznav_api._get_storage(db)
    _seed_feature(storage, "a", category="业务")
    _seed_feature(storage, "b", category="路由")
    resp = c.get("/biznav/features", params={"project_name": "demo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {f["id"] for f in body["features"]} == {"a", "b"}

    resp2 = c.get(
        "/biznav/features",
        params={"project_name": "demo", "category": "业务"},
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["total"] == 1
    assert body2["features"][0]["id"] == "a"


def test_update_feature_validates_version(client):
    c, db = client
    from agent.biznav import api as biznav_api
    storage = biznav_api._get_storage(db)
    _seed_feature(storage, "a")

    # 正确版本 → 成功
    resp = c.put(
        "/biznav/features/a",
        json={
            "project_name": "demo",
            "expected_version": 1,
            "name": "新名",
            "category": "业务更新",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "新名"
    assert body["version"] == 2
    assert body["source"] == "manual"  # PUT 后默认 manual

    # 用旧版本 → 409
    resp2 = c.put(
        "/biznav/features/a",
        json={
            "project_name": "demo",
            "expected_version": 1,
            "name": "再改",
        },
    )
    assert resp2.status_code == 409


def test_delete_feature(client):
    c, db = client
    from agent.biznav import api as biznav_api
    storage = biznav_api._get_storage(db)
    _seed_feature(storage, "a")
    # 软删除
    resp = c.delete("/biznav/features/a", params={"project_name": "demo"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["hard"] is False
    # 列表应不包含
    listed = storage.list_by_project("demo")
    assert listed == []

    # 硬删除
    _seed_feature(storage, "b")
    resp2 = c.delete(
        "/biznav/features/b",
        params={"project_name": "demo", "hard": "true"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["hard"] is True


def test_export_and_import(client):
    c, db = client
    from agent.biznav import api as biznav_api
    storage = biznav_api._get_storage(db)
    _seed_feature(storage, "a")
    _seed_feature(storage, "b", category="路由")

    # export YAML
    resp = c.get(
        "/biznav/export",
        params={"project_name": "demo", "project_root": "/tmp/demo", "format": "yaml"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "yaml"
    text = body["body"]
    assert "订单管理" in text

    # export JSON
    resp2 = c.get(
        "/biznav/export",
        params={"project_name": "demo", "format": "json"},
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["format"] == "json"
    import json
    items = json.loads(body2["body"])
    assert len(items) == 2

    # import：清空，再灌入 YAML（合并）
    storage.delete("a", "demo")
    storage.delete("b", "demo")
    resp3 = c.post(
        "/biznav/import",
        json={"project_name": "demo", "yaml_text": text, "merge": True},
    )
    assert resp3.status_code == 200
    rep = resp3.json()
    assert rep["ok"] is True
    assert rep["inserted"] == 2
    again = storage.list_by_project("demo")
    assert {f.id for f in again} == {"a", "b"}


def test_status_endpoint_empty(client):
    c, db = client
    resp = c.get("/biznav/status", params={"project_name": "nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_job"] is False
    assert body["project_name"] == "nope"


def test_affected_returns_empty_v11(client):
    c, db = client
    resp = c.get("/biznav/affected", params={"project_name": "demo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["affected"] == []


def test_reload_returns_503_v11(client):
    c, db = client
    resp = c.post("/biznav/reload", params={"project_name": "demo"})
    assert resp.status_code == 503
    assert "V1.1" in resp.json()["detail"]
