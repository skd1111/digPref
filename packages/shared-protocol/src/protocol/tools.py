"""ToolCall / ToolResult Pydantic models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ToolRiskLevel = Literal["read", "low", "medium", "high", "critical"]


class ToolCall(BaseModel):
    server: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk_level: ToolRiskLevel | None = Field(default=None, alias="riskLevel")
    target_system: str | None = Field(default=None, alias="targetSystem")


class ToolResultUi(BaseModel):
    """工具结果 UI 摘要：只给前端展示用，大结果体仍只进 LLM 上下文（result_spill）。"""

    summary: str | None = None
    icon: str | None = None
    path: str | None = None
    lines: int | None = None
    truncated: bool | None = None


class ToolResult(BaseModel):
    server: str
    name: str
    ok: bool
    data: object | None = None
    error: str | None = None
    truncated: bool | None = None
    rows_returned: int | None = Field(default=None, alias="rowsReturned")
    ui: ToolResultUi | None = None
