"""中央安全策略模块 — 已设计但暂未接入图执行流程。

当前 hitl_gate 直接调用 write_detector.is_write_call() 做写操作判断，
未经过本模块的 policy_for() 抽象层。保留此模块是为了：
  1. 未来引入 OPA / Rego 等外部策略引擎时，替换 hitl_gate 的判断逻辑；
  2. 细粒度风险分级（如 medium 级别自动批准、critical 需要多人签核）；
  3. 单元测试已有的策略逻辑不丢失。

接入图流程时，在 hitl_gate.py 中将 is_write_call() 替换为 policy_for()
即可启用此模块。

Centralised policy decisions — easy to unit-test, easy to swap.

A `PolicyDecision` is the only thing the hitl_gate node consumes; it doesn't
care how the decision was made. This lets us swap in OPA / Rego later
without touching the graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent.config import settings


DecisionKind = Literal["approve", "reject", "needs_hitl"]
RiskLevel = Literal["read", "low", "medium", "high", "critical"]


@dataclass(frozen=True)
class PolicyDecision:
    """Output of `policy_for(call)`."""
    decision: DecisionKind
    risk_level: RiskLevel
    reason: str = ""


# ---- Defaults --------------------------------------------------------------

# Risk level → minimum required approval scope. Currently a flat gate:
# any write requires HITL. Future: medium could be auto-approved with rate
# limit; critical could require multi-party sign-off.
_DEFAULT_RISK_THRESHOLD: RiskLevel = "low"


def policy_for(call: dict) -> PolicyDecision:
    """Decide whether the call can run without human approval.

    Parameters
    ----------
    call : dict
        Must carry `risk_level` (or have name/args that allow us to derive it).

    Returns
    -------
    PolicyDecision
    """
    risk = _derive_risk(call)

    # Critical operations ALWAYS require HITL, regardless of settings
    if risk == "critical":
        return PolicyDecision(
            decision="needs_hitl",
            risk_level=risk,
            reason="critical-risk operation requires explicit approval",
        )

    # Global toggle: turn off HITL entirely (NOT recommended in prod)
    if not settings.require_hitl_for_write:
        return PolicyDecision(decision="approve", risk_level=risk, reason="HITL disabled in config")

    # Reads are always auto-approved
    if risk == "read":
        return PolicyDecision(decision="approve", risk_level=risk, reason="read-only call")

    # Writes go to HITL
    return PolicyDecision(
        decision="needs_hitl",
        risk_level=risk,
        reason=f"write call (risk={risk}) requires HITL approval",
    )


def _derive_risk(call: dict) -> RiskLevel:
    """Pull a canonical risk level from the call, defaulting to 'read'."""
    # Only honour an explicit risk_level; the absence of the field means
    # "no planner claim", in which case we run our own heuristic.
    declared_raw = call.get("risk_level")
    if declared_raw is not None:
        declared = str(declared_raw).lower()
        if declared in {"read", "low", "medium", "high", "critical"}:
            return declared  # type: ignore[return-value]

    # Fall back to write_detector's classification
    from agent.safety.write_detector import is_write_call
    return "medium" if is_write_call(call) else "read"