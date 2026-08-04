"""Schemas for MCP tool invocations — kept in sync with shared-protocol."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


RiskLevel = Literal["read", "low", "medium", "high", "critical"]


class ToolCall(BaseModel):
    server: str
    name: str
    args: dict = Field(default_factory=dict)
    risk_level: RiskLevel = "read"
    target_system: str | None = None


class ToolResult(BaseModel):
    server: str
    name: str
    ok: bool
    data: object | None = None
    error: str | None = None
    truncated: bool = False
    rows_returned: int | None = None