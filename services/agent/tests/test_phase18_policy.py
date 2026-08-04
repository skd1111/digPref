"""Phase 18 ExecutionPolicy：子任务级执行策略。"""
from __future__ import annotations

from agent.dual.policy import ExecutionPolicy, build_policy, tag_plan_with_policy


def test_coding_policy_defaults():
    p = build_policy(framework="coding", validator_level="full")
    assert p.max_repair_attempts == 3
    assert p.framework == "coding"


def test_coding_policy_degrades_without_validator():
    assert build_policy("coding", "syntax_only").max_repair_attempts == 2
    assert build_policy("coding", "unverified").max_repair_attempts == 1


def test_work_policy_no_repair():
    assert build_policy("work", "full").max_repair_attempts == 0


def test_policy_autonomy_inherited():
    p = build_policy("work", "full", autonomy="auto")
    assert p.autonomy == "auto"


def test_policy_to_dict_roundtrip():
    p = build_policy("coding", "full")
    d = p.to_dict()
    assert d["framework"] == "coding"
    assert d["max_repair_attempts"] == 3
    assert d["validator_level"] == "full"


def test_tag_plan_single_routing():
    plan = [{"server": "builtin", "name": "write_file"}, {"server": "builtin", "name": "run_tests"}]
    policies = tag_plan_with_policy(plan, routing="coding", validator_level="full")
    assert len(policies) == len(plan)
    assert all(p["framework"] == "coding" for p in policies)


def test_tag_plan_mixed_routing_by_keyword():
    plan = [
        {"server": "builtin", "name": "write_file", "description": "写个导出脚本"},
        {"server": "database", "name": "run_sql", "description": "在数据库里跑报表"},
    ]
    policies = tag_plan_with_policy(plan, routing="mixed", validator_level="full")
    assert policies[0]["framework"] == "coding"
    assert policies[1]["framework"] == "work"


def test_tag_plan_empty():
    assert tag_plan_with_policy([], routing="coding") == []
