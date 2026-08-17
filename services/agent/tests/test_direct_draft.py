"""交付物模板直开草稿（零 LLM）回归测试（2026-08-14）。

覆盖：
  - ExpertMember.output_forms 序列化往返
  - POST /ops/case/drafts/direct：有模板直建草稿 / 幂等复用 / 无模板与非法入参拒绝
"""

from __future__ import annotations

import pytest
from agent.expert_teams.models import ExpertMember, ExpertTeam
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EAIDE_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))

    from agent.ops import api as ops_api

    ops_api._reset_storage_for_tests()
    ops_api._reset_case_storage_for_tests()
    app = FastAPI()
    app.include_router(ops_api.router)
    return TestClient(app)


def _team_with_forms() -> ExpertTeam:
    member = ExpertMember(
        name="客户身份识别专家",
        role="识别客户身份",
        outputs=["客户身份基本信息表", "身份风险疑点清单"],
        output_forms={
            "客户身份基本信息表": [
                {"name": "corp_name", "label": "企业名称", "type": "text", "required": True},
                {"name": "legal_person", "label": "法定代表人", "type": "text", "required": True},
                {"name": "license_file", "label": "营业执照", "type": "file", "required": False},
            ]
        },
    )
    return ExpertTeam(id="due_diligence_team", name="尽职调查专家团", members=[member])


@pytest.fixture
def fake_loader(client, monkeypatch):
    from agent.expert_teams import api as teams_api

    class _FakeLoader:
        def get(self, team_id: str):
            team = _team_with_forms()
            return team if team_id == team.id else None

    monkeypatch.setattr(teams_api, "_loader", _FakeLoader())


# ---- 模型层：output_forms 往返 ----------------------------------------------


def test_member_output_forms_roundtrip():
    m = ExpertMember.from_dict(
        {
            "name": "专家A",
            "role": "r",
            "outputs": ["表A"],
            "output_forms": {
                "表A": [{"name": "f1", "label": "字段一", "type": "text", "required": True}]
            },
        }
    )
    assert m.output_forms["表A"][0]["label"] == "字段一"
    d = m.to_dict()
    assert d["output_forms"]["表A"][0]["name"] == "f1"
    # 脏数据容忍：非 dict 字段被丢弃，不抛异常
    m2 = ExpertMember.from_dict(
        {"name": "专家B", "role": "r", "output_forms": {"表B": ["not-a-dict", 1]}}
    )
    assert m2.output_forms["表B"] == []


# ---- 端点层 -----------------------------------------------------------------


def _direct(
    client: TestClient, output: str = "客户身份基本信息表", member: str = "客户身份识别专家"
):
    return client.post(
        "/ops/case/drafts/direct",
        json={
            "case_id": "bank__ops_open",
            "team_id": "due_diligence_team",
            "member_key": member,
            "output_name": output,
        },
    )


def test_direct_draft_creates_form_without_llm(fake_loader, client):
    r = _direct(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reused"] is False
    draft = body["draft"]
    assert draft["title"] == "客户身份基本信息表"
    assert draft["status"] == "draft"
    # 模板字段与 yaml 定义一致（归一化后仍是 3 个字段）
    assert len(draft["template"]) == 3
    assert draft["values"] == {}


def test_direct_draft_idempotent_reuse(fake_loader, client):
    first = _direct(client).json()["draft"]
    second = _direct(client).json()
    assert second["reused"] is True
    assert second["draft"]["id"] == first["id"]
    # 草稿列表里只有一份（不重复建空表单）
    drafts = client.get("/ops/case/drafts", params={"case_id": "bank__ops_open"}).json()["drafts"]
    assert len(drafts) == 1


def test_direct_draft_no_template_rejected(fake_loader, client):
    r = _direct(client, output="身份风险疑点清单")  # outputs 里但没有 output_forms
    assert r.status_code == 400
    assert "未定义表单模板" in r.json()["detail"]


def test_direct_draft_invalid_refs_rejected(fake_loader, client):
    assert (
        client.post(
            "/ops/case/drafts/direct",
            json={
                "case_id": "bank__ops_open",
                "team_id": "ghost_team",
                "member_key": "客户身份识别专家",
                "output_name": "x",
            },
        ).status_code
        == 404
    )
    assert _direct(client, member="不存在的专家").status_code == 404
    assert _direct(client, output="不是交付物").status_code == 400
