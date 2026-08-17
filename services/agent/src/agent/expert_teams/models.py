"""ExpertMember / ExpertTeam 数据类。

专家团是系统一等资产（不寄生于 Skill）：团 + 成员两级结构化。
与 agent/skills/models.py 同风格（dataclass，V0 不进 shared-protocol）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExpertMember:
    """专家团内的单个专家角色。"""

    name: str  # 专家名称（必填）
    role: str  # 角色定位（必填）
    responsibilities: list[str] = field(default_factory=list)  # 主要职责
    focus_points: list[str] = field(default_factory=list)  # 关注点
    outputs: list[str] = field(default_factory=list)  # 典型输出
    # 交付物 → 表单模板（零 LLM 直开表单，2026-08-14）：
    # {交付物名: [{name, label, type, options, hint, required}]}，字段语义与
    # ops 草稿 template_json 一致（_ALLOWED_FIELD_TYPES）；未定义的交付物仍走
    # 问专家 → LLM 生成草稿链路。
    output_forms: dict[str, list[dict]] = field(default_factory=dict)
    prompt: str = ""  # 独立 prompt

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "responsibilities": list(self.responsibilities),
            "focus_points": list(self.focus_points),
            "outputs": list(self.outputs),
            "output_forms": {k: list(v) for k, v in self.output_forms.items()},
            "prompt": self.prompt,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExpertMember:
        raw_forms = data.get("output_forms", {})
        output_forms: dict[str, list[dict]] = {}
        if isinstance(raw_forms, dict):
            for key, fields in raw_forms.items():
                if isinstance(fields, list):
                    output_forms[str(key)] = [f for f in fields if isinstance(f, dict)]
        return cls(
            name=data["name"],
            role=data["role"],
            responsibilities=list(data.get("responsibilities", [])),
            focus_points=list(data.get("focus_points", [])),
            outputs=list(data.get("outputs", [])),
            output_forms=output_forms,
            prompt=data.get("prompt", ""),
        )


@dataclass
class ExpertTeam:
    """专家团（系统重要资产）。"""

    id: str  # slug（必填）
    name: str  # 中文名（必填）
    schema_version: str = "1.0"
    description: str = ""
    applicable_scenarios: list[str] = field(default_factory=list)  # 适用场景
    trigger_keywords: list[str] = field(default_factory=list)
    enabled: bool = True
    members: list[ExpertMember] = field(default_factory=list)
    report_template: str = ""  # 交付物报告模板文件名（templates/ 目录下，可选）

    # 运行时元数据（不持久化）
    source_path: str = ""
    loaded_at: int = 0

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "applicable_scenarios": list(self.applicable_scenarios),
            "trigger_keywords": list(self.trigger_keywords),
            "enabled": self.enabled,
            "members": [m.to_dict() for m in self.members],
            "report_template": self.report_template,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExpertTeam:
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            applicable_scenarios=list(data.get("applicable_scenarios", [])),
            trigger_keywords=list(data.get("trigger_keywords", [])),
            enabled=bool(data.get("enabled", True)),
            members=[
                ExpertMember.from_dict(m)
                for m in data.get("members", [])
                if isinstance(m, dict) and "name" in m and "role" in m
            ],
            report_template=str(data.get("report_template", "") or ""),
        )
