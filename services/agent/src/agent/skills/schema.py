"""Skill YAML JSON Schema 校验 + DSN 形态字符串检测。"""

from __future__ import annotations

import re
from typing import Any

import jsonschema

# DSN 形态字符串 pattern（CLAUDE.md §5 凭证保险箱契约）
_DSN_PATTERNS = [
    re.compile(r"jdbc:[a-z]+://", re.IGNORECASE),
    re.compile(r"mysql://", re.IGNORECASE),
    re.compile(r"postgres(?:ql)?://", re.IGNORECASE),
    re.compile(r"mongodb://", re.IGNORECASE),
    re.compile(r"redis://", re.IGNORECASE),
    re.compile(r"oracle://", re.IGNORECASE),
    re.compile(r"sqlserver://", re.IGNORECASE),
    re.compile(r"mariadb://", re.IGNORECASE),
]

SKILL_JSON_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Skill",
    "type": "object",
    "required": ["schema_version", "id", "name"],
    "properties": {
        "schema_version": {"type": "string", "pattern": r"^\d+\.\d+$"},
        "id": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{2,63}$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 64},
        "description": {"type": "string", "maxLength": 500},
        "version": {"type": "string"},
        "author": {"type": "string", "maxLength": 64},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
        "risk_level": {"enum": ["low", "medium", "high"]},
        "enabled": {"type": "boolean"},
        "trigger_keywords": {"type": "array", "items": {"type": "string"}},
        "mcp_servers": {"type": "array", "items": {"type": "string"}},
        "allowed_tools": {"type": "array", "items": {"type": "string"}},
        "system_prompt": {"type": "string", "maxLength": 4000},
        "few_shot_examples": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["role", "content"],
                "properties": {
                    "role": {"enum": ["user", "assistant"]},
                    "content": {"type": "string", "maxLength": 2000},
                },
            },
        },
        # 专家团预设（可选，旧 YAML 缺省不写也合法）
        "required_expert_team_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
        "materials": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
        "deliverables": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
    },
}


def validate_skill_yaml(data: dict) -> list[str]:
    """返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    validator = jsonschema.Draft7Validator(SKILL_JSON_SCHEMA)
    for err in validator.iter_errors(data):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors


def validate_no_dsn(data: dict) -> list[str]:
    """扫描 skill 所有字符串字段，检测 DSN 形态（jdbc:// / mysql:// 等）。
    返回违规字段的路径列表（空 = 通过）。"""
    violations: list[str] = []

    def check(path: str, node: Any) -> None:
        if isinstance(node, str):
            for pat in _DSN_PATTERNS:
                if pat.search(node):
                    violations.append(f"{path}: contains DSN pattern")
                    return
            return
        if isinstance(node, dict):
            for k, v in node.items():
                check(f"{path}.{k}" if path else k, v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                check(f"{path}[{i}]", v)

    check("", data)
    return violations


# 落库前脱敏用：与 _DSN_PATTERNS 同源，但把整段 DSN（含 user:pass@host 段）
# 直接替换掉，而不是仅检测（进化轨迹的摘要文本可能混入用户粘贴的连接串）
_DSN_SCRUB_RE = re.compile(
    r"(?:jdbc:[a-z]+|mysql|postgres(?:ql)?|mongodb|redis|oracle|sqlserver|mariadb)://\S+",
    re.IGNORECASE,
)
# user:password@host 形态凭证（不匹配普通 email —— 要求 @ 前有冒号密码段）
_CRED_SCRUB_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*:[^@\s:]+@[A-Za-z0-9.\-]+")


def scrub_dsn(text: str) -> str:
    """把文本中的 DSN / 凭证形态整段替换为占位符（落库前脱敏用）。"""
    if not text:
        return text
    out = _DSN_SCRUB_RE.sub("[REDACTED_DSN]", text)
    return _CRED_SCRUB_RE.sub("[REDACTED_CRED]", out)
