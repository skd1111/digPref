"""Skill 新增专家团预设三字段的向后兼容测试。"""

from agent.skills.models import Skill


def test_new_fields_default_empty():
    s = Skill.from_dict({"id": "abc_skill", "name": "x"})  # 旧 YAML 无新字段
    assert s.required_expert_team_ids == []
    assert s.materials == []
    assert s.deliverables == []


def test_new_fields_roundtrip():
    s = Skill.from_dict(
        {
            "id": "abc_skill",
            "name": "x",
            "required_expert_team_ids": ["due_diligence_team"],
            "materials": ["营业执照"],
            "deliverables": ["尽调报告初稿"],
        }
    )
    d = s.to_dict()
    assert d["required_expert_team_ids"] == ["due_diligence_team"]
    assert d["materials"] == ["营业执照"]
    assert d["deliverables"] == ["尽调报告初稿"]


def test_schema_accepts_new_fields():
    from agent.skills.schema import validate_skill_yaml

    errs = validate_skill_yaml(
        {
            "schema_version": "1.0",
            "id": "abc_skill",
            "name": "x",
            "required_expert_team_ids": ["t"],
            "materials": ["m"],
            "deliverables": ["d"],
        }
    )
    assert errs == []
