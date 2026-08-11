"""reqflow.models —— 需求批次 / 需求卡片数据类 + 状态机（V1）。

设计：
- 全 dataclass + field(default_factory=list)；零外部依赖
- 状态流转 V1 由业务人员手动切换；`can_transition()` 独立函数，
  将来审批模式接入时只替换该函数为事件驱动，表和前端不动（审批预留口）
- feature_ids / external_systems 存 JSON 数组，to_dict/from_dict 直接吐 list
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---- 状态常量 ---------------------------------------------------------------

DRAFT = "draft"
PENDING_APPROVAL = "pending_approval"
APPROVED = "approved"
DEVELOPING = "developing"
DONE = "done"
REJECTED = "rejected"

ALL_STATUSES = (DRAFT, PENDING_APPROVAL, APPROVED, DEVELOPING, DONE, REJECTED)

STATUS_LABEL: dict[str, str] = {
    DRAFT: "草稿",
    PENDING_APPROVAL: "待审批",
    APPROVED: "已批准",
    DEVELOPING: "开发中",
    DONE: "已完成",
    REJECTED: "已驳回",
}

# ---- 状态机 -----------------------------------------------------------------

# 审批预留口：V1 手动切换；将来审批模式接管时只替换 can_transition 实现
_ALLOWED: dict[str, tuple[str, ...]] = {
    DRAFT: (PENDING_APPROVAL, REJECTED),
    PENDING_APPROVAL: (APPROVED, REJECTED),
    APPROVED: (DEVELOPING, REJECTED),
    DEVELOPING: (DONE, REJECTED),
    DONE: (),
    REJECTED: (),
}


def can_transition(current: str, target: str) -> bool:
    """状态流转校验：非法流转返回 False（调用方抛 409/ValueError）。"""
    return target in _ALLOWED.get(current, ())


# ---- 数据类 -----------------------------------------------------------------


@dataclass
class ReqBatch:
    """需求批次（一次需求收集周期，如「2026-08 优化批次」）。"""

    id: str
    name: str
    project_name: str
    status: str = "open"  # open / closed
    created_by: str = ""
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "project_name": self.project_name,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReqBatch:
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            project_name=str(d.get("project_name", "")),
            status=str(d.get("status", "open")),
            created_by=str(d.get("created_by", "")),
            created_at=int(d.get("created_at", 0)),
            updated_at=int(d.get("updated_at", 0)),
        )


@dataclass
class ReqCard:
    """需求卡片：业务人员与 AI 对齐后的结构化改造需求。"""

    id: str
    batch_id: str
    project_name: str
    system_name: str
    title: str
    feature_ids: list[str] = field(default_factory=list)
    business_value: str = ""
    change_points: str = ""
    feasibility: str = ""  # feasible / risky / infeasible
    feasibility_notes: str = ""
    impact: str = ""
    external_systems: list[str] = field(default_factory=list)
    priority: str = "P2"  # P0 / P1 / P2
    status: str = DRAFT
    conversation_summary: str = ""
    session_id: str = ""
    approved_by: str | None = None
    approved_at: int | None = None
    version: int = 1  # 每次修改 +1；快照存 req_card_versions，默认展示最新版
    created_by: str = ""
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "project_name": self.project_name,
            "system_name": self.system_name,
            "title": self.title,
            "feature_ids": list(self.feature_ids),
            "business_value": self.business_value,
            "change_points": self.change_points,
            "feasibility": self.feasibility,
            "feasibility_notes": self.feasibility_notes,
            "impact": self.impact,
            "external_systems": list(self.external_systems),
            "priority": self.priority,
            "status": self.status,
            "conversation_summary": self.conversation_summary,
            "session_id": self.session_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "version": self.version,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReqCard:
        return cls(
            id=str(d.get("id", "")),
            batch_id=str(d.get("batch_id", "")),
            project_name=str(d.get("project_name", "")),
            system_name=str(d.get("system_name", "")),
            title=str(d.get("title", "")),
            feature_ids=[str(x) for x in (d.get("feature_ids") or [])],
            business_value=str(d.get("business_value", "")),
            change_points=str(d.get("change_points", "")),
            feasibility=str(d.get("feasibility", "")),
            feasibility_notes=str(d.get("feasibility_notes", "")),
            impact=str(d.get("impact", "")),
            external_systems=[str(x) for x in (d.get("external_systems") or [])],
            priority=str(d.get("priority", "P2")),
            status=str(d.get("status", DRAFT)),
            conversation_summary=str(d.get("conversation_summary", "")),
            session_id=str(d.get("session_id", "")),
            approved_by=d.get("approved_by"),
            approved_at=d.get("approved_at"),
            version=int(d.get("version", 1)),
            created_by=str(d.get("created_by", "")),
            created_at=int(d.get("created_at", 0)),
            updated_at=int(d.get("updated_at", 0)),
        )
