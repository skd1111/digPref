"""Phase 7 补齐 · Schema 真实同步测试（缺口 2）。

覆盖：
  - build_schema_query：6 方言族元数据 SQL 均为 SELECT（只读）
  - normalize_schema_rows：行 → [{name, comment, columns}] 归一
  - sync_schema 端点：mock pool.fetch_schema → schema_cache 落库
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from agent.config import settings
from agent.dataexpert.readonly.pool import build_schema_query, normalize_schema_rows
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_build_schema_query_dialects():
    """全部方言族的元数据查询都是 SELECT（只读铁律）。"""
    for db in (
        "mysql",
        "tidb",
        "oceanbase",
        "gbase",
        "postgresql",
        "kingbase",
        "gaussdb",
        "opengauss",
        "highgo",
        "oracle",
        "dm",
        "sqlserver",
        "clickhouse",
    ):
        sql = build_schema_query(db)
        assert sql.lstrip().upper().startswith("SELECT"), f"{db} 元数据查询必须是 SELECT"


def test_build_schema_query_unknown_falls_back():
    """未知类型回退 sqlite_master 风格查询（仍是 SELECT）。"""
    sql = build_schema_query("sqlite")
    assert sql.lstrip().upper().startswith("SELECT")


def test_normalize_schema_rows():
    rows = [
        ("t_order", "订单表", "id", "bigint", "主键"),
        ("t_order", "订单表", "status", "varchar(8)", "状态"),
        ("t_user", "", "uid", "int", ""),
    ]
    tables = normalize_schema_rows(rows)
    assert len(tables) == 2
    t_order = tables[0]
    assert t_order["name"] == "t_order"
    assert t_order["comment"] == "订单表"
    assert t_order["columns"][1] == {"name": "status", "dtype": "varchar(8)", "comment": "状态"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_expert_db_path", str(tmp_path / "d.db"))
    monkeypatch.setattr(settings, "env", "prod")
    from agent.dataexpert.storage import reset_default_storage

    reset_default_storage()
    from agent.dataexpert.api import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c
    reset_default_storage()


def test_sync_schema_persists_cache(client):
    """sync 端点：fetch_schema 结果写入 schema_cache。"""
    storage_tables = [
        {
            "name": "t_order",
            "comment": "订单表",
            "columns": [{"name": "id", "dtype": "bigint", "comment": "主键"}],
        },
    ]

    # 先登记数据源（TestClient 的 portal 在同事件循环执行异步调用）
    from agent.dataexpert.storage import get_default_storage

    async def _seed():
        await get_default_storage().upsert_source(
            source_id="src1",
            name="核心账务",
            source_type="mysql",
            connection_ref="",
            schema_cache=[],
            updated_at=0,
        )

    client.portal.call(_seed)  # type: ignore[attr-defined]

    with patch("agent.dataexpert.api.ReadOnlyPool") as pool_cls:
        pool_cls.return_value.fetch_schema = AsyncMock(return_value=storage_tables)
        pool_cls.return_value.close = AsyncMock()
        r = client.post(
            "/data/sources/src1/sync",
            json={"connection": {"type": "mysql", "host": "127.0.0.1"}},
        )
    body = r.json()
    assert body["ok"] is True
    assert body["tables_synced"] == 1

    # schema_cache 已落库
    r2 = client.get("/data/sources").json()
    src = next(s for s in r2["sources"] if s["id"] == "src1")
    assert src["schema_cache"][0]["name"] == "t_order"


def test_sync_schema_unknown_source_404(client):
    r = client.post("/data/sources/nope/sync", json={})
    assert r.status_code == 404
