"""dict.models —— 数据字典条目数据类（Phase 2H）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DictItem:
    """一条公共参数/业务字典。key 全局唯一，Skill 通过 key 引用。"""

    key: str
    category: str = "通用"
    label: str = ""
    value: str = ""
    description: str = ""
    source: str = "manual"  # 'seed' | 'manual'
    updated_by: str = ""
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category,
            "label": self.label,
            "value": self.value,
            "description": self.description,
            "source": self.source,
            "updated_by": self.updated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DictItem:
        return cls(
            key=str(d.get("key", "")),
            category=str(d.get("category", "通用")),
            label=str(d.get("label", "")),
            value=str(d.get("value", "")),
            description=str(d.get("description", "")),
            source=str(d.get("source", "manual")),
            updated_by=str(d.get("updated_by", "")),
            created_at=int(d.get("created_at", 0)),
            updated_at=int(d.get("updated_at", 0)),
        )
