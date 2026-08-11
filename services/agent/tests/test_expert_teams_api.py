"""FastAPI /expert-teams/* 路由 + ExpertTeamLoader 测试。"""

import pytest
import yaml
from agent.expert_teams import api as api_mod
from agent.expert_teams.loader import ExpertTeamLoader
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _valid_dict() -> dict:
    return {
        "schema_version": "1.0",
        "id": "due_diligence_team",
        "name": "尽职调查专家团",
        "description": "贷前尽调多专家协同",
        "applicable_scenarios": ["对公信贷贷前尽调"],
        "trigger_keywords": ["尽调", "贷前"],
        "enabled": True,
        "members": [
            {
                "name": "尽调项目经理",
                "role": "统筹整个尽调任务",
                "responsibilities": ["判断尽调类型"],
                "focus_points": ["关键资料缺失不得进入报告生成"],
                "outputs": ["尽调任务书"],
                "prompt": "你是尽职调查项目经理。",
            }
        ],
    }


@pytest.fixture
def client(tmp_path):
    """patch api._loader 指向 tmp loader，构造 TestClient（同 test_skills_api）。"""
    loader = ExpertTeamLoader(tmp_path / "eaide" / "expert_teams")
    saved_loader = api_mod._loader
    api_mod._loader = loader
    app = FastAPI()
    app.include_router(api_mod.router)
    try:
        yield TestClient(app)
    finally:
        api_mod._loader = saved_loader


# ---- Loader ----


def test_loader_scan_and_get(tmp_path):
    (tmp_path / "due_diligence_team.yaml").write_text(
        yaml.safe_dump(_valid_dict(), allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "broken.yaml").write_text("not: [a, valid", encoding="utf-8")
    loader = ExpertTeamLoader(tmp_path)
    teams = loader.load_all()
    assert len(teams) == 1  # 坏文件隔离
    assert loader.get("due_diligence_team") is not None
    assert loader.get("due_diligence_team").source_path.endswith(".yaml")
    loader.remove("due_diligence_team")
    assert loader.get("due_diligence_team") is None


# ---- API ----


def test_list_empty(client):
    assert client.get("/expert-teams/list").json() == {"teams": []}


def test_import_get_delete_roundtrip(client):
    r = client.post("/expert-teams/import", json=_valid_dict())
    assert r.status_code == 200 and r.json()["ok"]
    teams = client.get("/expert-teams/list").json()["teams"]
    assert len(teams) == 1 and teams[0]["id"] == "due_diligence_team"
    assert client.get("/expert-teams/due_diligence_team").json()["name"] == "尽职调查专家团"
    assert client.delete("/expert-teams/due_diligence_team").json()["ok"]
    assert client.get("/expert-teams/due_diligence_team").status_code == 404


def test_import_conflict(client):
    client.post("/expert-teams/import", json=_valid_dict())
    assert client.post("/expert-teams/import", json=_valid_dict()).status_code == 409


def test_import_yaml_text(client):
    content = yaml.safe_dump(_valid_dict(), allow_unicode=True)
    r = client.post("/expert-teams/import", json={"content": content})
    assert r.status_code == 200 and r.json()["ok"]


def test_import_bad_yaml_text(client):
    r = client.post("/expert-teams/import", json={"content": "not: [a, valid"})
    assert r.status_code == 400


def test_save_upsert(client):
    body = _valid_dict()
    body["name"] = "改名"
    r = client.put("/expert-teams/due_diligence_team", json=body)
    assert r.status_code == 200
    assert client.get("/expert-teams/due_diligence_team").json()["name"] == "改名"


def test_save_id_mismatch(client):
    body = _valid_dict()
    r = client.put("/expert-teams/other_team", json=body)
    assert r.status_code == 400


def test_import_dsn_rejected(client):
    d = _valid_dict()
    d["description"] = "x postgres://a@b/c"
    assert client.post("/expert-teams/import", json=d).status_code == 400


def test_export_all(client):
    client.post("/expert-teams/import", json=_valid_dict())
    data = client.get("/expert-teams/export/all").json()
    assert "due_diligence_team" in data["teams"]


def test_recommend_preset(client):
    client.post("/expert-teams/import", json=_valid_dict())
    r = client.post(
        "/expert-teams/recommend",
        json={"preset_team_ids": ["due_diligence_team"], "feature_name": "任意业务"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["team_ids"] == ["due_diligence_team"]
    assert body["source"] == "preset"


def test_recommend_keyword_fallback(client):
    """LLM 全挂（monkeypatch 抛错）→ 关键词命中。"""
    import agent.expert_teams.recommender as rec_mod

    async def _boom(teams, query):
        raise RuntimeError("all llm down")

    saved = rec_mod._llm_recommend
    rec_mod._llm_recommend = _boom
    try:
        client.post("/expert-teams/import", json=_valid_dict())
        r = client.post(
            "/expert-teams/recommend",
            json={"feature_name": "贷前尽调业务", "preset_team_ids": []},
        )
        body = r.json()
        assert body["team_ids"] == ["due_diligence_team"]
        assert body["source"] == "keyword"
    finally:
        rec_mod._llm_recommend = saved
