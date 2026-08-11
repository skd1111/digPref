"""test_biznav_rule_engine.py —— BusinessRule 校验 + system prompt 片段（Phase 2G V1.1）。

测试矩阵（6 个）：
- test_validate_empty_text_returns_error
- test_validate_normal_text_ok
- test_validate_overlong_text_returns_error
- test_to_system_prompt_snippet_format
- test_storage_upsert_rejects_invalid_business_rule
- test_to_system_prompt_with_structured_placeholder
"""

from __future__ import annotations

import pytest
from agent.biznav.models import BusinessRule, Feature, RelatedFile
from agent.biznav.rule_engine import (
    BUSINESS_RULE_MAX_LEN,
    to_system_prompt_snippet,
    validate_syntax,
)
from agent.biznav.storage import FeatureStorage


def test_validate_empty_text_returns_error(tmp_path):
    errs = validate_syntax(BusinessRule(text=""))
    assert any("non-empty" in e for e in errs)
    errs2 = validate_syntax(BusinessRule(text="   "))
    assert any("non-empty" in e for e in errs2)


def test_validate_normal_text_ok(tmp_path):
    errs = validate_syntax(BusinessRule(text="订单必须经过风控校验"))
    assert errs == []


def test_validate_overlong_text_returns_error(tmp_path):
    long = "x" * (BUSINESS_RULE_MAX_LEN + 1)
    errs = validate_syntax(BusinessRule(text=long))
    assert any("too long" in e for e in errs)


def test_to_system_prompt_snippet_format(tmp_path):
    rules = [
        BusinessRule(text="订单必须经过风控校验"),
        BusinessRule(text="退款 24 小时内处理"),
        "纯字符串规则",
    ]
    snippet = to_system_prompt_snippet(rules)
    assert snippet.startswith("业务规则：")
    assert "1. 订单必须经过风控校验" in snippet
    assert "2. 退款 24 小时内处理" in snippet
    assert "3. 纯字符串规则" in snippet


def test_storage_upsert_rejects_invalid_business_rule(tmp_path):
    storage = FeatureStorage(str(tmp_path / "biznav.db"))
    f = Feature(
        id="feat-1",
        name="订单",
        description="",
        category="业务",
        project_name="demo",
        project_root="/tmp",
        related_files=[RelatedFile(path="src/X.java")],
        business_rules=[BusinessRule(text="")],  # 空字符串 → 校验失败
        source="ai",
        version=1,
    )
    with pytest.raises(ValueError):
        storage.upsert(f)


def test_to_system_prompt_with_structured_placeholder(tmp_path):
    rules = [
        BusinessRule(
            text="订单金额上限 100 万",
            structured={"field": "amount", "op": "<=", "value": 1000000},
        ),
    ]
    snippet = to_system_prompt_snippet(rules)
    assert "订单金额上限 100 万" in snippet
    # NOTE: V1.5 才会把 structured 渲染到 snippet；V1.1 只用 text
