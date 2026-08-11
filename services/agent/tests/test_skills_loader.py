"""Loader 单元测试。"""

import pytest
from agent.skills.loader import SkillLoader

VALID_YAML = """
schema_version: "1.0"
id: db_query_order
name: 订单库查询
description: 查询订单
trigger_keywords: [订单, order]
mcp_servers: [mcp-server-database]
allowed_tools: [db.query]
system_prompt: 你是一名订单分析师
"""


@pytest.fixture
def tmp_skills_dir(tmp_path):
    """返回临时 skills 目录，预先写好一个有效 YAML。"""
    d = tmp_path / "skills"
    d.mkdir()
    (d / "db_query_order.yaml").write_text(VALID_YAML, encoding="utf-8")
    (d / "broken.yaml").write_text("id: bad\nname: x", encoding="utf-8")  # 缺 schema_version
    return d


def test_load_all_reads_yaml_files(tmp_skills_dir):
    loader = SkillLoader(tmp_skills_dir)
    skills = loader.load_all()
    assert len(skills) == 1  # broken.yaml 被跳过
    assert skills[0].id == "db_query_order"


def test_load_all_creates_missing_dir(tmp_path):
    d = tmp_path / "nonexistent" / "skills"
    loader = SkillLoader(d)
    skills = loader.load_all()
    assert d.exists()
    assert skills == []


def test_load_one_updates_index(tmp_skills_dir):
    loader = SkillLoader(tmp_skills_dir)
    loader.load_all()
    assert "db_query_order" in [s.id for s in loader.list()]

    new_yaml = tmp_skills_dir / "new_skill.yaml"
    new_yaml.write_text(VALID_YAML.replace("db_query_order", "new_skill"), encoding="utf-8")
    skill = loader.load_one(new_yaml)
    assert skill is not None
    assert skill.id == "new_skill"
    assert "new_skill" in [s.id for s in loader.list()]


def test_load_one_invalid_returns_none(tmp_skills_dir):
    loader = SkillLoader(tmp_skills_dir)
    bad = tmp_skills_dir / "invalid.yaml"
    bad.write_text("id: bad\nname: x", encoding="utf-8")
    result = loader.load_one(bad)
    assert result is None


def test_remove_skill(tmp_skills_dir):
    loader = SkillLoader(tmp_skills_dir)
    loader.load_all()
    assert "db_query_order" in [s.id for s in loader.list()]
    loader.remove("db_query_order")
    assert "db_query_order" not in [s.id for s in loader.list()]


def test_get_skill_by_id(tmp_skills_dir):
    loader = SkillLoader(tmp_skills_dir)
    loader.load_all()
    s = loader.get("db_query_order")
    assert s is not None
    assert s.id == "db_query_order"
    assert s.name == "订单库查询"
