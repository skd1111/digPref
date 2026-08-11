"""Audit entry — mirrors the SQLite row from src-tauri + services/agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AuditAction = Literal[
    "agent.run.start",
    "agent.run.error",
    "agent.approval",
    "agent.cancel",
    "approval.request",
    "approval.decision",
    "credential.set",
    "db.execute",
]


class AuditEntry(BaseModel):
    id: int
    action: AuditAction | str
    payload: object
    ts: datetime
    operator: str | None = None
    run_id: str | None = Field(default=None, alias="runId")
