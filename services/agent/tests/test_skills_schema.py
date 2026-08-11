"""Schema 校验测试。"""

from agent.skills.schema import validate_no_dsn, validate_skill_yaml

VALID_SKILL = {
    "schema_version": "1.0",
    "id": "db_query_order",
    "name": "订单库查询",
    "description": "查询订单",
    "trigger_keywords": ["订单", "order"],
    "mcp_servers": ["mcp-server-database"],
    "allowed_tools": ["db.query"],
    "system_prompt": "你是一名订单分析师",
    "few_shot_examples": [
        {"role": "user", "content": "查订单"},
        {"role": "assistant", "content": "好的"},
    ],
}


def test_validate_skill_yaml_valid():
    errors = validate_skill_yaml(VALID_SKILL)
    assert errors == []


def test_validate_skill_yaml_missing_id():
    bad = {**VALID_SKILL}
    del bad["id"]
    errors = validate_skill_yaml(bad)
    assert any("id" in e for e in errors)


def test_validate_skill_yaml_invalid_id_pattern():
    bad = {**VALID_SKILL, "id": "Invalid-ID-With-Dashes"}
    errors = validate_skill_yaml(bad)
    assert any("id" in e for e in errors)


def test_validate_skill_yaml_name_too_long():
    bad = {**VALID_SKILL, "name": "x" * 100}
    errors = validate_skill_yaml(bad)
    assert any("name" in e for e in errors)


def test_validate_no_dsn_clean():
    assert validate_no_dsn(VALID_SKILL) == []


def test_validate_no_dsn_postgres():
    bad = {**VALID_SKILL, "mcp_servers": ["postgresql://user:pass@host/db"]}
    errors = validate_no_dsn(bad)
    assert len(errors) > 0


def test_validate_no_dsn_mysql():
    bad = {**VALID_SKILL, "system_prompt": "使用 mysql://root@localhost 连接"}
    errors = validate_no_dsn(bad)
    assert len(errors) > 0


def test_validate_no_dsn_jdbc():
    bad = {**VALID_SKILL, "description": "jdbc:mysql://server"}
    errors = validate_no_dsn(bad)
    assert len(errors) > 0
