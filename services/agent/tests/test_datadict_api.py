"""test_datadict_api.py —— 数据字典 FastAPI 路由测试（Phase 2H）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "dict.db")
    audit_db = str(tmp_path / "audit.sqlite")
    monkeypatch.setenv("EAIDE_DICT_DB", db)
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", audit_db)

    from agent.datadict import api as dict_api

    dict_api._reset_storage_for_tests()
    app = FastAPI()
    app.include_router(dict_api.router)
    return TestClient(app)


def test_seed_items_present(client):
    r = client.get("/dict/items")
    body = r.json()
    assert body["total"] >= 7
    keys = {i["key"] for i in body["items"]}
    assert "authorization_valid_days" in keys
    assert "large_cash_threshold" in keys


def test_categories(client):
    r = client.get("/dict/categories")
    cats = r.json()["categories"]
    assert "授权" in cats and "对公" in cats


def test_create_update_search_delete(client):
    r = client.post(
        "/dict/items",
        json={
            "key": "k1",
            "category": "测试",
            "label": "参数甲",
            "value": "v1",
            "description": "说明",
        },
    )
    assert r.status_code == 200
    assert r.json()["source"] == "manual"

    # 重复 key → 409
    r = client.post("/dict/items", json={"key": "k1", "label": "x"})
    assert r.status_code == 409

    # 搜索
    r = client.get("/dict/search", params={"q": "参数甲"})
    assert r.json()["total"] == 1
    r = client.get("/dict/search", params={"q": "v1"})
    assert r.json()["total"] == 1

    # 更新
    r = client.put("/dict/items/k1", json={"value": "v2"})
    assert r.status_code == 200
    assert r.json()["value"] == "v2"

    # 删除
    assert client.delete("/dict/items/k1").status_code == 200
    assert client.delete("/dict/items/k1").status_code == 404


def test_seed_update_and_delete(client):
    # seed 条目可显式编辑（覆盖后转 manual）
    r = client.put(
        "/dict/items/authorization_valid_days",
        json={"value": "10"},
    )
    assert r.status_code == 200
    assert r.json()["value"] == "10"
    assert r.json()["source"] == "manual"

    # seed 条目可删除
    assert client.delete("/dict/items/authorization_valid_days").status_code == 200


def test_missing_key_404(client):
    assert client.put("/dict/items/nope", json={"value": "x"}).status_code == 404
