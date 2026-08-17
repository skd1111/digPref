"""专家团 YAML JSON Schema 校验。DSN 检测复用 skills 体系。"""

from __future__ import annotations

import jsonschema

# DSN 形态字符串检测与 Skill 完全一致（CLAUDE.md §5 凭证保险箱契约）
from agent.skills.schema import validate_no_dsn

EXPERT_TEAM_JSON_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ExpertTeam",
    "type": "object",
    "required": ["schema_version", "id", "name"],
    "properties": {
        "schema_version": {"type": "string", "pattern": r"^\d+\.\d+$"},
        "id": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{2,63}$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 64},
        "description": {"type": "string", "maxLength": 500},
        "applicable_scenarios": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "trigger_keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "enabled": {"type": "boolean"},
        "report_template": {"type": "string", "maxLength": 256},
        "members": {
            "type": "array",
            "maxItems": 30,
            "items": {
                "type": "object",
                "required": ["name", "role"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "role": {"type": "string", "minLength": 1, "maxLength": 500},
                    "responsibilities": {"type": "array", "items": {"type": "string"}},
                    "focus_points": {"type": "array", "items": {"type": "string"}},
                    "outputs": {"type": "array", "items": {"type": "string"}},
                    "output_forms": {
                        "type": "object",
                        "maxProperties": 30,
                        "additionalProperties": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {
                                "type": "object",
                                "required": ["name", "label"],
                                "properties": {
                                    "name": {"type": "string", "minLength": 1, "maxLength": 64},
                                    "label": {"type": "string", "minLength": 1, "maxLength": 64},
                                    "type": {"type": "string"},
                                    "options": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "maxItems": 20,
                                    },
                                    "hint": {"type": "string", "maxLength": 200},
                                    "required": {"type": "boolean"},
                                },
                            },
                        },
                    },
                    "prompt": {"type": "string", "maxLength": 4000},
                },
            },
        },
    },
}


def validate_expert_team_yaml(data: dict) -> list[str]:
    """返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    validator = jsonschema.Draft7Validator(EXPERT_TEAM_JSON_SCHEMA)
    for err in validator.iter_errors(data):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors


__all__ = [
    "EXPERT_TEAM_JSON_SCHEMA",
    "validate_expert_team_yaml",
    "validate_no_dsn",
]
