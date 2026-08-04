"""Conditional edges — declarative wiring of the LangGraph nodes.

Topology:

                       ┌──────────┐
                       │  START   │
                       └────┬─────┘
                            ▼
                       ┌────────┐
                       │ intent │
                       └────┬───┘
                            ▼
                       ┌────────┐
              ┌────────│planner │────────┐
              │        └────────┘        │
              │ (empty plan)              │ (have plan)
              ▼                           ▼
        ┌──────────┐               ┌────────────┐
        │ responder│               │tool_runner │
        └──────────┘               └─────┬──────┘
                                         │
                                error?  │  ok
                          ┌──────────────┼─────────────────┐
                          ▼              ▼                 ▼
                     ┌────────┐   ┌───────────┐     ┌─────────────┐
                     │ repair │──▶│hitl_gate  │────▶│ next iter   │
                     └────────┘   └─────┬─────┘     │ (tool_runner│
                              retry 2x   │           │  + advance) │
                              exhausted ▼           └─────────────┘
                                    ┌─────────┐
                                    │ responder│
                                    └─────────┘
"""
from __future__ import annotations

from agent.config import settings
from agent.graph.state import AgentState, StepStatus


# ---- Edge functions --------------------------------------------------------

def route_after_planner(state: AgentState) -> str:
    """planner → tool_runner (have plan) | responder (empty)."""
    if state.get("plan"):
        return "tool_runner"
    return "responder"


def route_after_decompose(state: AgentState) -> str:
    """decompose → responder (终态模式/确认门槛/多智能体) | tool_runner (TOOL_ONLY) | responder."""
    if state.get("multi_agent") or state.get("sub_agent_reports"):
        return "responder"
    decision = state.get("decompose_decision") or {}
    inner = decision.get("decision") if isinstance(decision, dict) else {}
    if isinstance(inner, dict):
        mode = inner.get("mode")
        if mode == "TOOL_ONLY":
            # 2026-08-03：工具任务 → 动态工具循环（摘要 → 候选 → 全量兜底）；
            # 测试 / 回退模式（tool_loop_enabled=false）走既有 tool_runner
            if getattr(settings, "tool_loop_enabled", True):
                return "tool_orchestrator"
            if state.get("plan"):
                return "tool_runner"
            return "responder"
        if mode in ("MAIN_AGENT", "ASK_USER", "REFUSE"):
            return "responder"
        if inner.get("user_confirmation_required") is True:
            return "responder"
    if state.get("plan"):
        return "tool_runner"
    return "responder"


def route_after_tool_loop(state: AgentState) -> str:
    """tool_orchestrator → hitl_gate (写/高危审批) | responder (完成) | 自身 (继续循环)。"""
    if state.get("awaiting_approval"):
        return "hitl_gate"
    if state.get("final_answer") is not None or state.get("tool_loop_active") is False:
        return "responder"
    return "tool_orchestrator"


def route_after_tool(state: AgentState) -> str:
    """tool_runner → repair (error) | hitl_gate (ok)."""
    if state.get("tool_error"):
        return "repair"
    return "hitl_gate"


def route_after_repair(state: AgentState) -> str:
    """repair → tool_runner (re-attempt) | responder (give up).

    Repair cleared `tool_error` on success → retry. Otherwise → responder.
    """
    if state.get("tool_error"):
        return "responder"
    return "tool_runner"


def route_after_hitl(state: AgentState) -> str:
    """hitl_gate 路由 —— 含审批等待循环。

    审批中（awaiting_approval=True） → 回到 hitl_gate 继续等待
    拒绝/超时                        → responder
    批准 + 还有后续步骤               → tool_runner
    批准 + 最后一步                   → responder
    """
    # 仍在等待审批 → 循环回 hitl_gate 检查决策
    if state.get("awaiting_approval"):
        return "hitl_gate"

    # 2026-08-03：动态工具循环内的审批 —— 批准/拒绝都回循环（结果由循环注入上下文）
    if state.get("tool_loop_active"):
        return "tool_orchestrator"

    decision = state.get("approval_decision")
    if decision == "reject":
        return "responder"
    plan = state.get("plan") or []
    idx = state.get("current_step_index", 0)
    if idx < len(plan):
        return "tool_runner"
    return "responder"
