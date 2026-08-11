"""推荐器单元测试：preset / llm(mock) / keyword / none。"""

import agent.expert_teams.recommender as rec_mod
from agent.expert_teams.models import ExpertTeam
from agent.expert_teams.recommender import _parse_llm_text, recommend_team


def _team(team_id: str, keywords: list[str], enabled: bool = True) -> ExpertTeam:
    return ExpertTeam.from_dict(
        {
            "schema_version": "1.0",
            "id": team_id,
            "name": team_id,
            "applicable_scenarios": [],
            "trigger_keywords": keywords,
            "enabled": enabled,
            "members": [{"name": "专家", "role": "r"}],
        }
    )


async def test_preset_wins():
    teams = [_team("t_a", [])]
    r = await recommend_team(teams, preset_ids=["t_a"], feature_name="x")
    assert r["team_ids"] == ["t_a"]
    assert r["source"] == "preset"


async def test_preset_filters_disabled_and_unknown():
    teams = [_team("t_a", [], enabled=False)]
    r = await recommend_team(teams, preset_ids=["t_a", "ghost"], feature_name="x")
    # 预设全部无效 → 继续走后续链路（无关键词 → none）
    assert r["team_ids"] == []
    assert r["source"] == "none"


async def test_llm_hit(monkeypatch):
    async def _fake_llm(teams, query):
        return ("t_b", 0.9, "描述匹配")

    monkeypatch.setattr(rec_mod, "_llm_recommend", _fake_llm)
    teams = [_team("t_a", []), _team("t_b", [])]
    r = await recommend_team(teams, preset_ids=[], feature_name="某业务")
    assert r["team_ids"] == ["t_b"]
    assert r["source"] == "llm"
    assert r["confidence"] == 0.9


async def test_llm_fail_falls_back_to_keyword(monkeypatch):
    async def _boom(teams, query):
        raise RuntimeError("all llm down")

    monkeypatch.setattr(rec_mod, "_llm_recommend", _boom)
    teams = [_team("t_a", ["开户"]), _team("t_b", ["尽调", "贷前"])]
    r = await recommend_team(teams, preset_ids=[], feature_name="对公贷前尽调")
    assert r["team_ids"] == ["t_b"]  # 命中 2 个关键词 > 0 个
    assert r["source"] == "keyword"


async def test_no_match(monkeypatch):
    async def _none(teams, query):
        return None

    monkeypatch.setattr(rec_mod, "_llm_recommend", _none)
    teams = [_team("t_a", ["开户"])]
    r = await recommend_team(teams, preset_ids=[], feature_name="完全无关的业务")
    assert r["team_ids"] == []
    assert r["source"] == "none"


def test_parse_llm_text_valid():
    teams = [_team("t_a", [])]
    hit = _parse_llm_text('{"team_id": "t_a", "confidence": 0.7, "reasoning": "ok"}', teams)
    assert hit == ("t_a", 0.7, "ok")


def test_parse_llm_text_unknown_id():
    teams = [_team("t_a", [])]
    assert _parse_llm_text('{"team_id": "ghost"}', teams) is None


def test_parse_llm_text_garbage():
    teams = [_team("t_a", [])]
    assert _parse_llm_text("我不知道", teams) is None
