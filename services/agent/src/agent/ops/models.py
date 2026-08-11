"""ops.models —— 业务记录卡片数据类（Phase 2H）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 记录结果状态
DONE = "done"  # 已完成
PENDING = "pending"  # 进行中/待办
REJECTED = "rejected"  # 未受理/退回
FOLLOW_UP = "follow_up"  # 需跟进

ALL_RESULTS = (DONE, PENDING, REJECTED, FOLLOW_UP)

RESULT_LABEL: dict[str, str] = {
    DONE: "已完成",
    PENDING: "进行中",
    REJECTED: "未受理",
    FOLLOW_UP: "需跟进",
}


@dataclass
class BusinessRecord:
    """一笔业务的总结卡片（可审计、可统计）。"""

    id: str
    project_name: str = ""
    feature_id: str = ""
    business_type: str = ""
    title: str = ""
    summary: str = ""
    materials_checked: list[str] = field(default_factory=list)
    materials_missing: list[str] = field(default_factory=list)
    risk_points: list[str] = field(default_factory=list)
    result: str = DONE
    skill_id: str = ""
    session_id: str = ""
    source: str = "ai"  # 'ai' | 'manual'
    created_by: str = ""
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_name": self.project_name,
            "feature_id": self.feature_id,
            "business_type": self.business_type,
            "title": self.title,
            "summary": self.summary,
            "materials_checked": list(self.materials_checked),
            "materials_missing": list(self.materials_missing),
            "risk_points": list(self.risk_points),
            "result": self.result,
            "skill_id": self.skill_id,
            "session_id": self.session_id,
            "source": self.source,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BusinessRecord:
        return cls(
            id=str(d.get("id", "")),
            project_name=str(d.get("project_name", "")),
            feature_id=str(d.get("feature_id", "")),
            business_type=str(d.get("business_type", "")),
            title=str(d.get("title", "")),
            summary=str(d.get("summary", "")),
            materials_checked=[str(x) for x in (d.get("materials_checked") or [])],
            materials_missing=[str(x) for x in (d.get("materials_missing") or [])],
            risk_points=[str(x) for x in (d.get("risk_points") or [])],
            result=str(d.get("result", DONE)),
            skill_id=str(d.get("skill_id", "")),
            session_id=str(d.get("session_id", "")),
            source=str(d.get("source", "ai")),
            created_by=str(d.get("created_by", "")),
            created_at=int(d.get("created_at", 0)),
            updated_at=int(d.get("updated_at", 0)),
        )
