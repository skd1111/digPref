"""Phase 7 v2.87 · MetricResolver API 端点测试。

覆盖：
  - POST /data/metric/resolve（命中 / 未命中 / 配置错误）
  - GET  /data/metric/list（默认 dict / 占位 NotImplementedError）
  - POST /data/nl2sql 集成 MetricResolver（NL2SQLResponse 新增 metric_source_kind / metric_confidence 字段）

与 ``test_metric_resolver.py``（单元测试 MetricResolver 内部）互补 —— 本文件专注 HTTP 契约。
"""

from __future__ import annotations

import pytest
from agent.config import settings
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_expert_db_path", str(tmp_path / "d.db"))
    monkeypatch.setattr(settings, "data_result_dir", str(tmp_path / "results"))
    monkeypatch.setattr(settings, "env", "prod")
    from agent.dataexpert.storage import reset_default_storage

    reset_default_storage()
    from agent.dataexpert.api import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c
    reset_default_storage()


# =============================================================================
# POST /data/metric/resolve
# =============================================================================


def test_metric_resolve_hit_returns_resolved(client):
    """业务字典命中 → 返回 ResolvedQuery。"""
    r = client.post(
        "/data/metric/resolve",
        json={"question": "查询成功订单的总额", "source_id": ""},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == ""
    resolved = body["resolved"]
    assert resolved is not None
    assert resolved["source_kind"] == "dict"
    assert 0.0 <= resolved["confidence"] <= 1.0
    assert resolved["metric"]["name"] != ""
    # ResolvedQuery 完整字段
    assert "metric" in resolved
    assert "dimensions_filter" in resolved
    assert "candidates" in resolved


def test_metric_resolve_miss_returns_null(client):
    """业务字典未命中 → resolved=None（前端可回退纯 NL2SQL）。"""
    r = client.post(
        "/data/metric/resolve",
        json={"question": "今天是星期几"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] is None
    assert body["error"] == ""


def test_metric_resolve_with_source_id(client):
    """source_id='ds_credit' → 加载该数据源业务字典。"""
    r = client.post(
        "/data/metric/resolve",
        json={"question": "统计正常类贷款", "source_id": "ds_credit"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] is not None
    assert body["resolved"]["source_kind"] == "dict"


def test_metric_resolve_unknown_resolver_type(monkeypatch):
    """配置错误（unknown type）→ error 字段兜底 + resolved=None。"""
    monkeypatch.setenv("EAIDE_METRIC_RESOLVER", "elasticsearch")
    from agent.dataexpert.storage import reset_default_storage

    reset_default_storage()
    from agent.dataexpert.api import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        r = c.post("/data/metric/resolve", json={"question": "test"})
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] is None
    assert "配置错误" in body["error"]
    assert "elasticsearch" in body["error"]


# =============================================================================
# GET /data/metric/list
# =============================================================================


def test_metric_list_default_dict(client):
    """默认 DictMetricResolver → 列出 _global 字典条目。"""
    r = client.get("/data/metric/list")
    assert r.status_code == 200
    body = r.json()
    assert body["source_kind"] == "dict"
    assert isinstance(body["metrics"], list)
    assert len(body["metrics"]) >= 5
    # 每个 metric 是 MetricDef
    for m in body["metrics"]:
        assert "code" in m
        assert "name" in m
        assert "agg" in m
        assert "dimensions" in m


def test_metric_list_with_project_param(client):
    """?project=xxx 参数被忽略（V0 dict 模式不支持项目隔离）。"""
    r = client.get("/data/metric/list?project=demo")
    assert r.status_code == 200
    assert r.json()["source_kind"] == "dict"


# =============================================================================
# POST /data/nl2sql 集成 MetricResolver
# =============================================================================


def test_nl2sql_response_includes_metric_source_kind(client):
    """v2.87：NL2SQLResponse 新增 metric_source_kind + metric_confidence 字段。"""
    r = client.post(
        "/data/nl2sql",
        json={"question": "查询成功订单的总数", "source_id": ""},
    )
    assert r.status_code == 200
    body = r.json()
    # V0 dict 模式：业务字典命中"成功" → source_kind='dict'
    assert body["metric_source_kind"] == "dict"
    assert 0.0 <= body["metric_confidence"] <= 1.0
    # 既有字段不变
    assert "sql" in body
    assert "is_heavy" in body
    assert "tables_used" in body


def test_nl2sql_response_empty_when_metric_not_recognized(client):
    """业务字典未命中 → metric_source_kind=''（前端按空值隐藏状态栏）。"""
    r = client.post(
        "/data/nl2sql",
        json={"question": "今天是星期几", "source_id": ""},
    )
    assert r.status_code == 200
    body = r.json()
    # NL2SQL 流程本身仍跑（linker + generator），但 metric_source_kind 为空
    assert body["metric_source_kind"] == ""
    assert body["metric_confidence"] == 0.0


def test_nl2sql_response_includes_metric_fields_on_write_blocked(client):
    """写操作拦截时 NL2SQLResponse 仍透传 metric_source_kind / metric_confidence。"""
    # 模拟生成 SQL 含 UPDATE（实际由测试 fixture + enforce_select_only 拦截）
    # 这里只验证字段在 NL2SQLResponse 模型里有定义 —— 实际拦截逻辑由 test_data_guard 覆盖
    r = client.post(
        "/data/nl2sql",
        json={"question": "DROP TABLE t", "source_id": ""},
    )
    assert r.status_code == 200
    body = r.json()
    # 字段必须存在（哪怕 value 是空字符串）
    assert "metric_source_kind" in body
    assert "metric_confidence" in body
