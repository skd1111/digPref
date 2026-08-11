"""test_ops_api.py —— 运营工作台业务记录 FastAPI 路由测试（Phase 2H）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "ops.db")
    audit_db = str(tmp_path / "audit.sqlite")
    monkeypatch.setenv("EAIDE_OPS_DB", db)
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", audit_db)

    from agent.ops import api as ops_api

    ops_api._reset_storage_for_tests()
    app = FastAPI()
    app.include_router(ops_api.router)
    return TestClient(app)


def _create_record(client: TestClient, title: str = "对公开户") -> dict:
    r = client.post(
        "/ops/records",
        json={
            "project_name": "bank",
            "feature_id": "ops_corp_open",
            "business_type": "对公开户",
            "title": title,
            "summary": "客户资料齐全，已完成开户受理。",
            "materials_checked": ["营业执照", "法人身份证"],
            "materials_missing": ["章程原件"],
            "risk_points": ["受益所有人信息待补"],
            "result": "pending",
            "skill_id": "corp_open",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_create_and_list_records(client):
    rec = _create_record(client)
    assert rec["id"].startswith("OPR-")
    assert rec["result"] == "pending"
    assert rec["materials_missing"] == ["章程原件"]

    r = client.get("/ops/records", params={"feature_id": "ops_corp_open"})
    body = r.json()
    assert body["total"] == 1
    assert body["records"][0]["title"] == "对公开户"

    # 无匹配 → 空列表
    r = client.get("/ops/records", params={"feature_id": "nope"})
    assert r.json()["total"] == 0


def test_get_and_delete_record(client):
    rec = _create_record(client, "贷后检查")
    r = client.get(f"/ops/records/{rec['id']}")
    assert r.status_code == 200
    assert r.json()["title"] == "贷后检查"

    assert client.delete(f"/ops/records/{rec['id']}").status_code == 200
    assert client.get(f"/ops/records/{rec['id']}").status_code == 404


def test_invalid_result_rejected(client):
    r = client.post(
        "/ops/records",
        json={"title": "x", "result": "bogus"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_summarize_record_ok(client, monkeypatch):
    from agent.ops import api as ops_api

    async def fake_llm(messages):
        return (
            '{"title": "对公开户受理完成", "business_type": "对公开户", '
            '"summary": "客户提供全套开户资料，已完成资料预审。", '
            '"materials_checked": ["营业执照", "法人身份证"], '
            '"materials_missing": ["受益所有人说明"], '
            '"risk_points": ["受益所有人识别待人工复核"], "result": "pending"}'
        )

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: fake_llm)
    r = client.post(
        "/ops/records/summarize",
        json={
            "feature_id": "ops_corp_open",
            "project_name": "bank",
            "business_type": "对公开户",
            "conversation": [
                {"role": "user", "content": "客户来开对公户，资料带齐了吗？"},
                {"role": "assistant", "content": "还缺受益所有人说明。"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["title"] == "对公开户受理完成"
    assert draft["result"] == "pending"
    assert draft["materials_missing"] == ["受益所有人说明"]


@pytest.mark.asyncio
async def test_summarize_record_llm_fail(client, monkeypatch):
    from agent.ops import api as ops_api

    async def broken(_messages):
        raise RuntimeError("所有 LLM 后端均不可用（mock）")

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: broken)
    r = client.post(
        "/ops/records/summarize",
        json={"feature_id": "f1", "project_name": "bank", "conversation": []},
    )
    assert r.status_code == 502
    assert "LLM" in r.json()["detail"]


@pytest.mark.asyncio
async def test_summarize_record_non_json(client, monkeypatch):
    from agent.ops import api as ops_api

    async def garbage(_messages):
        return "not json at all"

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: garbage)
    r = client.post(
        "/ops/records/summarize",
        json={"feature_id": "f1", "project_name": "bank", "conversation": []},
    )
    assert r.status_code == 502
