"""test_reqflow_api.py —— reqflow FastAPI 路由测试（V1）。

策略同 test_biznav_api.py：fresh FastAPI 子 app + TestClient，storage 走 tmp_path。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "reqcards.db")
    audit_db = str(tmp_path / "audit.sqlite")
    biznav_db = str(tmp_path / "biznav.db")
    monkeypatch.setenv("EAIDE_REQCARDS_DB", db)
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", audit_db)
    monkeypatch.setenv("EAIDE_BIZNAV_DB", biznav_db)

    from agent.reqflow import api as reqflow_api

    reqflow_api._reset_storage_for_tests()
    app = FastAPI()
    app.include_router(reqflow_api.router)
    return TestClient(app)


def _create_batch(client: TestClient, name: str = "B1") -> str:
    r = client.post("/reqflow/batches", json={"project_name": "proj", "name": name})
    assert r.status_code == 200, r.text
    return str(r.json()["id"])


def _create_card(client: TestClient, batch_id: str, title: str = "A") -> dict:
    r = client.post(
        "/reqflow/cards",
        json={
            "batch_id": batch_id,
            "project_name": "proj",
            "system_name": "订单系统",
            "title": title,
            "feature_ids": ["f1"],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---- 批次 -------------------------------------------------------------------


def test_create_and_list_batches(client):
    batch_id = _create_batch(client, "2026-08 批次")
    assert batch_id.startswith("BAT-")
    r = client.get("/reqflow/batches", params={"project_name": "proj"})
    body = r.json()
    assert len(body["batches"]) == 1
    assert body["batches"][0]["name"] == "2026-08 批次"
    assert body["stats"][batch_id]["total"] == 0


# ---- 卡片 CRUD + 状态 + 版本 --------------------------------------------------


def test_card_crud_and_versioning(client):
    batch_id = _create_batch(client)
    card = _create_card(client, batch_id)
    card_id = card["id"]
    assert card_id.startswith("REQ-")
    assert card["version"] == 1

    # 合法修改 → version+1
    r = client.put(f"/reqflow/cards/{card_id}", json={"title": "新标题"})
    assert r.status_code == 200
    assert r.json()["version"] == 2

    # 版本列表 + 快照
    r = client.get(f"/reqflow/cards/{card_id}/versions")
    assert r.status_code == 200
    assert [v["version"] for v in r.json()["versions"]] == [1]
    r = client.get(f"/reqflow/cards/{card_id}/versions/1")
    assert r.json()["snapshot"]["title"] == "A"
    # 不存在的版本 → 404
    assert client.get(f"/reqflow/cards/{card_id}/versions/99").status_code == 404

    # 列表过滤
    r = client.get("/reqflow/cards", params={"batch_id": batch_id})
    assert r.json()["total"] == 1
    r = client.get("/reqflow/cards", params={"feature_id": "f1"})
    assert r.json()["total"] == 1
    r = client.get("/reqflow/cards", params={"feature_id": "nope"})
    assert r.json()["total"] == 0


def test_card_status_transition_rules(client):
    batch_id = _create_batch(client)
    card_id = _create_card(client, batch_id)["id"]
    r = client.put(f"/reqflow/cards/{card_id}", json={"status": "pending_approval"})
    assert r.status_code == 200
    # 跳级 → 409
    r = client.put(f"/reqflow/cards/{card_id}", json={"status": "done"})
    assert r.status_code == 409
    # 批准时填 approved_by/at（审批预留字段）
    r = client.put(
        f"/reqflow/cards/{card_id}",
        json={"status": "approved", "changed_by": "张三"},
    )
    assert r.status_code == 200
    assert r.json()["approved_by"] == "张三"
    assert r.json()["approved_at"]


def test_delete_only_draft(client):
    batch_id = _create_batch(client)
    card_id = _create_card(client, batch_id)["id"]
    client.put(f"/reqflow/cards/{card_id}", json={"status": "pending_approval"})
    assert client.delete(f"/reqflow/cards/{card_id}").status_code == 409
    card_id2 = _create_card(client, batch_id, "B")["id"]
    assert client.delete(f"/reqflow/cards/{card_id2}").status_code == 200
    r = client.get("/reqflow/cards", params={"batch_id": batch_id})
    assert r.json()["total"] == 1


def test_get_card_404(client):
    assert client.put("/reqflow/cards/NOPE", json={"title": "x"}).status_code == 404


# ---- AI 生成 ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_card_draft_ok(client, monkeypatch):
    from agent.reqflow import api as reqflow_api

    async def fake_generate(**kwargs):
        return {
            "title": "订单部分取消",
            "business_value": "v",
            "change_points": "c",
            "feasibility": "feasible",
            "feasibility_notes": "n",
            "impact": "i",
            "external_systems": ["支付网关"],
            "priority": "P1",
        }

    monkeypatch.setattr(reqflow_api.generator, "generate_card_draft", fake_generate)
    _create_batch(client)
    r = client.post(
        "/reqflow/cards/generate",
        json={
            "feature_ids": ["f1"],
            "project_name": "proj",
            "system_name": "订单系统",
            "conversation_summary": "用户想支持部分取消",
        },
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["title"] == "订单部分取消"
    assert draft["feasibility"] == "feasible"


@pytest.mark.asyncio
async def test_generate_card_draft_llm_all_fail(client, monkeypatch):
    from agent.reqflow import api as reqflow_api

    async def broken(**kwargs):
        raise RuntimeError("所有 LLM 后端均不可用（mock）")

    monkeypatch.setattr(reqflow_api.generator, "generate_card_draft", broken)
    r = client.post(
        "/reqflow/cards/generate",
        json={"feature_ids": [], "project_name": "proj", "conversation_summary": "x"},
    )
    assert r.status_code == 502
    assert "LLM" in r.json()["detail"]


# ---- 导出 -------------------------------------------------------------------


def test_export_markdown(client):
    batch_id = _create_batch(client, "导出批次")
    _create_card(client, batch_id, "需求甲")
    r = client.get("/reqflow/export", params={"batch_id": batch_id, "format": "md"})
    assert r.status_code == 200
    md = r.json()["markdown"]
    assert "# 需求文档 · 导出批次" in md and "需求甲" in md


def test_export_docx(client):
    batch_id = _create_batch(client, "导出批次")
    _create_card(client, batch_id, "需求甲")
    r = client.get("/reqflow/export", params={"batch_id": batch_id, "format": "docx"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument")
    assert r.content[:2] == b"PK"


def test_export_bad_format(client):
    batch_id = _create_batch(client)
    assert (
        client.get("/reqflow/export", params={"batch_id": batch_id, "format": "pdf"}).status_code
        == 400
    )
