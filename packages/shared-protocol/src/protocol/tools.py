"""ToolCall / ToolResult Pydantic models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ToolRiskLevel = Literal["read", "low", "medium", "high", "critical"]


class ToolCall(BaseModel):
    server: str
    name: str
    args: dict = Field(default_factory=dict)
    risk_level: ToolRiskLevel | None = Field(default=None, alias="riskLevel")
    target_system: str | None = Field(default=None, alias="targetSystem")


class ToolResult(BaseModel):
    server: str
    name: str
    ok: bool
    data: object | None = None
    error: str | None = None
    truncated: bool | None = None
    rows_returned: int | None = Field(default=None, alias="rowsReturned")