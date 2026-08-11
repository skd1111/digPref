"""Skill / FewShotExample / SkillRoutingResult 数据类。

V0 不进 shared-protocol（Python + TS 各自实现，V1 镜像）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

RiskLevel = Literal["low", "medium", "high"]
FewShotRole = Literal["user", "assistant"]


@dataclass
class FewShotExample:
    role: FewShotRole
    content: str


@dataclass
class Skill:
    id: str  # slug（必填）
    name: str  # 中文名（必填）
    schema_version: str = "1.0"
    description: str = ""
    version: str = "1.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)
    risk_level: RiskLevel = "low"
    enabled: bool = True

    trigger_keywords: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    role: str = "utility"  # V1: utility / reasoning / execution（与 2C LLMBackend.role 同名）

    system_prompt: str = ""
    few_shot_examples: list[FewShotExample] = field(default_factory=list)

    # 专家团预设（可选，旧 YAML 缺省空数组，向后兼容）：
    # 本业务默认专家团 / 办理材料清单 / 最终交付物清单
    required_expert_team_ids: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)

    # 运行时元数据（V0 不持久化）
    source_path: str = ""
    loaded_at: int = 0
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "tags": list(self.tags),
            "risk_level": self.risk_level,
            "enabled": self.enabled,
            "trigger_keywords": list(self.trigger_keywords),
            "mcp_servers": list(self.mcp_servers),
            "allowed_tools": list(self.allowed_tools),
            "role": self.role,
            "system_prompt": self.system_prompt,
            "few_shot_examples": [
                {"role": e.role, "content": e.content} for e in self.few_shot_examples
            ],
            "required_expert_team_ids": list(self.required_expert_team_ids),
            "materials": list(self.materials),
            "deliverables": list(self.deliverables),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Skill:
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0"),
            author=data.get("author", ""),
            tags=list(data.get("tags", [])),
            risk_level=data.get("risk_level", "low"),
            enabled=bool(data.get("enabled", True)),
            trigger_keywords=list(data.get("trigger_keywords", [])),
            mcp_servers=list(data.get("mcp_servers", [])),
            allowed_tools=list(data.get("allowed_tools", [])),
            role=data.get("role", "utility"),  # V1: utility / reasoning / execution
            system_prompt=data.get("system_prompt", ""),
            few_shot_examples=[
                FewShotExample(role=e["role"], content=e["content"])
                for e in data.get("few_shot_examples", [])
                if isinstance(e, dict) and "role" in e and "content" in e
            ],
            required_expert_team_ids=list(data.get("required_expert_team_ids", [])),
            materials=list(data.get("materials", [])),
            deliverables=list(data.get("deliverables", [])),
        )


@dataclass
class SkillRoutingResult:
    skill_id: str | None  # None = 无匹配
    skill_name: str = ""  # 冗余方便 SSE payload
    confidence: float = 0.0  # 0-1
    matched_keywords: list[str] = field(default_factory=list)
