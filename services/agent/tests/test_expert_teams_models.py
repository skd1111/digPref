"""ExpertTeam / ExpertMember 模型与 schema 校验。"""

from __future__ import annotations

from agent.expert_teams.models import ExpertMember, ExpertTeam
from agent.expert_teams.schema import validate_expert_team_yaml, validate_no_dsn


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
                "responsibilities": ["判断尽调类型", "生成尽调计划"],
                "focus_points": ["关键资料缺失不得进入报告生成"],
                "outputs": ["尽调任务书", "资料清单"],
                "prompt": "你是尽职调查项目经理。",
            }
        ],
    }


def test_from_dict_roundtrip():
    team = ExpertTeam.from_dict(_valid_dict())
    assert team.id == "due_diligence_team"
    assert len(team.members) == 1
    assert team.members[0].name == "尽调项目经理"
    d = team.to_dict()
    assert d["members"][0]["responsibilities"] == ["判断尽调类型", "生成尽调计划"]


def test_member_optional_fields_default_empty():
    m = ExpertMember.from_dict({"name": "财务分析专家", "role": "财务分析"})
    assert m.responsibilities == []
    assert m.focus_points == []
    assert m.outputs == []
    assert m.prompt == ""


def test_schema_ok():
    assert validate_expert_team_yaml(_valid_dict()) == []


def test_schema_missing_required():
    d = _valid_dict()
    del d["id"]
    errs = validate_expert_team_yaml(d)
    assert any("id" in e for e in errs)


def test_schema_bad_member():
    d = _valid_dict()
    d["members"][0].pop("name")
    errs = validate_expert_team_yaml(d)
    assert errs


def test_dsn_rejected():
    d = _valid_dict()
    d["description"] = "连接 mysql://user@host/db"
    assert validate_no_dsn(d)
