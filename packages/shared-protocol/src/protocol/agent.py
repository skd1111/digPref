"""ChatMessage, TraceStep, AgentRun — Pydantic mirrors of the TS types."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from protocol.approval import ApprovalRequest


ChatRole = Literal["user", "assistant", "tool", "system"]
AgentRunStatus = Literal["idle", "running", "awaiting_approval", "done", "error", "cancelled"]
ExecutionStatus = Literal["running", "ok", "err"]


class ChatMessage(BaseModel):
    id: str
    role: ChatRole
    content: str
    code: str | None = None
    code_lang: Literal["sql", "json", "python", "bash"] | None = Field(default=None, alias="codeLang")
    pending_approval: ApprovalRequest | None = Field(default=None, alias="pendingApproval")
    # Phase 12 V1: Codex/Claude 风格执行链路 — execution kind 让前端渲染成折叠 step 块
    kind: Literal["normal", "execution"] | None = None
    category: str | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    status: ExecutionStatus | None = None
    run_id: str | None = Field(default=None, alias="runId")


class AgentRun(BaseModel):
    run_id: str = Field(alias="runId")
    status: AgentRunStatus
    started_at: datetime = Field(alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    error: str | None = None


class TraceStep(BaseModel):
    id: str
    node: str
    status: Literal["ok", "fail", "running"]
    duration_ms: int = Field(alias="durationMs")
    summary: str | None = None