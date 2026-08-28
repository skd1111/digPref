"""HITL approval models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from protocol.tools import ToolCall, ToolRiskLevel

# approve_always（2026-08-25）：批准且本会话内同工具（同 server·name）
# 后续操作自动放行；硬阻断（DROP/TRUNCATE）任何决策都不可豁免。
ApprovalDecision = Literal["approve", "reject", "approve_always"]


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
