"""MCP server / tool metadata models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


McpServerStatus = Literal["stopped", "starting", "ready", "error"]


class McpServerInfo(BaseModel):
    name: str
    status: McpServerStatus
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] | None = None
    error: str | None = None


class McpToolSpec(BaseModel):
    server: str
    name: str
    description: str | None = None
    input_schema: dict = {}