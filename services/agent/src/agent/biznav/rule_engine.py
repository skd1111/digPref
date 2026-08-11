"""biznav.rule_engine —— 业务规则校验 + system prompt 注入（Phase 2G V1.1）。

V1.1 极简：仅校验 text 非空 + 长度 < 500。V1.5 上结构化字段（structured）
再扩 JSON Schema 校验。

调用方：
- storage.upsert() 录入前对每条 BusinessRule 调 validate_syntax
- extractor / api 把 to_system_prompt_snippet 注入 LLM context
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import BusinessRule

# 长度上限（V1.1 简单阈值；V1.5 拆细：单条件 / 跨条件）
BUSINESS_RULE_MAX_LEN = 500


def validate_syntax(rule: BusinessRule) -> list[str]:
    """返回错误列表。空列表 = 通过。

    V1.1 规则：
      - text.strip() 非空
      - len(text) < 500

    type check: BusinessRule dataclass 实例默认 truthy，无需 `not rule` 防御。
    调用方负责传入正确类型 —— `storage.upsert` 也已约定。
    """
    errors: list[str] = []
    if not isinstance(rule, BusinessRule):
        return [f"rule must be a BusinessRule instance (got {type(rule).__name__})"]
    if not rule.text or not rule.text.strip():
        errors.append("business rule text must be non-empty")
    if rule.text and len(rule.text) > BUSINESS_RULE_MAX_LEN:
        errors.append(
            f"business rule text too long (len={len(rule.text)} > {BUSINESS_RULE_MAX_LEN})"
        )
    return errors


def to_system_prompt_snippet(
    rules: Iterable[BusinessRule | str],
) -> str:
    """把规则列表拼成 system prompt 片段。

    输出格式：
        业务规则：
        1. ...
        2. ...
    """
    lines: list[str] = []
    idx = 0
    for r in rules:
        if isinstance(r, BusinessRule):
            text = r.text.strip()
        else:
            text = str(r).strip()
        if not text:
            continue
        idx += 1
        lines.append(f"{idx}. {text}")
    if not lines:
        return ""
    return "业务规则：\n" + "\n".join(lines)
