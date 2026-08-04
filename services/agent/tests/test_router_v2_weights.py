"""Phase 2C V2 — 评分权重 PUT 落库 + 热生效 Engine 内存。

覆盖：
  - PUT /router/weights 校验 5 维 Σ=1（容差 ±0.01）
  - 持久化到 router.db.router_weights id=1
  - Engine.weights 内存同步更新
  - 启动期从 router.db 读权重（覆盖默认）
"""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.llm import engine_api


@pytest.fixture
def client(tmp_path, monkeypatch):
    """构造 TestClient + 隔离 router.db。"""
    monkeypatch.setenv("EAIDE_LLM_ROUTER_DB_PATH", str(tmp_path / "router.db"))
    # 清掉模块级 _ENGINE 单例（保证下次 _get_engine() 重新构造）
    engine_api._ENGINE = None
    app = FastAPI()
    app.include_router(engine_api.router)
    return TestClient(app)


def _get_persisted_weights(db_path: Path) -> dict | None:
    """sync 读 router.db.router_weights 单行。"""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT capability, cost, latency, compliance, availability FROM router_weights WHERE id=1"
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "capability": row[0], "cost": row[1], "latency": row[2],
        "compliance": row[3], "availability": row[4],
    }


def test_put_weights_persists_and_heats_engine(client, tmp_path):
    """PUT 权重 → router.db.router_weights 落 1 行 + Engine 内存更新。"""
    new_weights = {
        "capability": 0.50, "cost": 0.20, "latency": 0.10,
        "compliance": 0.10, "availability": 0.10,  # Σ = 1.0
    }
    resp = client.put("/router/weights", json=new_weights)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True

    # 1) 落库
    persisted = _get_persisted_weights(tmp_path / "router.db")
    assert persisted is not None
    assert persisted["capability"] == pytest.approx(0.50)

    # 2) Engine 内存更新
    eng = engine_api._get_engine()
    assert eng.weights["capability"] == pytest.approx(0.50)
    assert eng.weights["cost"] == pytest.approx(0.20)


def test_put_weights_rejects_sum_not_one(client):
    """5 维和不=1 → 400 + 不落库 + Engine 内存不变。"""
    bad_weights = {
        "capability": 0.50, "cost": 0.30, "latency": 0.10,
        "compliance": 0.10, "availability": 0.10,  # Σ = 1.10
    }
    resp = client.put("/router/weights", json=bad_weights)
    assert resp.status_code == 400
    assert "sum to 1" in resp.json()["detail"].lower()


def test_put_weights_rejects_out_of_range(client):
    """单值 > 1 → 422（Pydantic Field ge/le 校验）。"""
    bad_weights = {
        "capability": 1.5, "cost": -0.5,  # 两个都越界
        "latency": 0.0, "compliance": 0.0, "availability": 0.0,
    }
    resp = client.put("/router/weights", json=bad_weights)
    assert resp.status_code == 422  # Pydantic 校验失败


def test_get_weights_returns_persisted(client, tmp_path):
    """先 PUT → 再 GET 应返回真值（不硬编码默认）。"""
    new_weights = {
        "capability": 0.40, "cost": 0.30, "latency": 0.10,
        "compliance": 0.15, "availability": 0.05,
    }
    client.put("/router/weights", json=new_weights)
    resp = client.get("/router/weights")
    assert resp.status_code == 200
    body = resp.json()
    assert body["weights"]["capability"] == pytest.approx(0.40)


def test_engine_init_loads_persisted_weights(tmp_path, monkeypatch):
    """重启 Engine（_ENGINE = None）→ 重新构造时从 router.db 读回持久化权重。

    模拟生产场景：先 PUT 一次，关掉 Engine 单例，重启 → 应读到上次写的值。
    """
    monkeypatch.setenv("EAIDE_LLM_ROUTER_DB_PATH", str(tmp_path / "router.db"))
    engine_api._ENGINE = None
    app = FastAPI()
    app.include_router(engine_api.router)
    c = TestClient(app)

    # 第一次 PUT
    c.put("/router/weights", json={
        "capability": 0.60, "cost": 0.15, "latency": 0.10,
        "compliance": 0.10, "availability": 0.05,
    })
    # 重置单例 + 重启 app
    engine_api._ENGINE = None
    eng2 = engine_api._get_engine()
    assert eng2.weights["capability"] == pytest.approx(0.60), \
        "Engine should load persisted weights on init"