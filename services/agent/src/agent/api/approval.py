"""/approval endpoint — receives HITL decisions and writes to Redis.

This does NOT directly resume the LangGraph run; the `await_approval()`
coroutine inside `hitl_gate_node` polls Redis every 250ms and resumes
when a decision appears. Decoupling the API from the runtime lets the
Tauri frontend decide on its own cadence.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from agent.audit.store import audit
from agent.graph.interrupt import post_decision

router = APIRouter(prefix="/approval", tags=["approval"])


class ApprovalDecision(BaseModel):
    decision: str  # "approve" | "reject"
    operator: str | None = None


@router.post("/{approval_id}")
async def decide(approval_id: str, body: ApprovalDecision) -> dict:
    if body.decision not in ("approve", "reject"):
        return {
            "approval_id": approval_id,
            "ok": False,
            "error": "decision must be 'approve' or 'reject'",
        }
    await post_decision(approval_id, body.decision)
    await audit(
        "approval.decision",
        {
            "approval_id": approval_id,
            "decision": body.decision,
            "operator": body.operator,
        },
    )
    return {"approval_id": approval_id, "ok": True, "decision": body.decision}
