"""HITL approval models."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from protocol.tools import ToolCall, ToolRiskLevel


ApprovalDecision = Literal["approve", "reject"]


class ApprovalOption(BaseModel):
    """Phase 18：审批候选选项（Work 框架推荐选项机制）。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    adjusted_plan: str = Field(alias="adjustedPlan")
    risk_note: str | None = Field(default=None, alias="riskNote")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    run_id: str = Field(alias="runId")
    plan: ToolCall
    risk_level: ToolRiskLevel = Field(alias="riskLevel")
    reason: str | None = None
    created_at: datetime = Field(alias="createdAt")
    # Phase 18 双框架：推荐选项（为空 = 保持二元审批，向后兼容）
    options: list[ApprovalOption] = Field(default_factory=list)
    recommended_option_id: str | None = Field(default=None, alias="recommendedOptionId")
    recommendation_reason: str | None = Field(default=None, alias="recommendationReason")


class PendingApproval(ApprovalRequest):
    """Reserved for future divergence from ApprovalRequest."""