"""Tests for graph/edges.py — conditional routing."""

from __future__ import annotations

from agent.graph.edges import (
    route_after_hitl,
    route_after_planner,
    route_after_repair,
    route_after_tool,
)
from agent.graph.state import empty_state

# ---- route_after_planner --------------------------------------------------


class TestRouteAfterPlanner:
    def test_with_plan_goes_to_tool(self):
        s = empty_state("x")
        s["plan"] = [{"server": "db", "name": "db.query"}]
        assert route_after_planner(s) == "tool_runner"

    def test_without_plan_goes_to_responder(self):
        s = empty_state("x")
        s["plan"] = []
        assert route_after_planner(s) == "responder"


# ---- route_after_tool -----------------------------------------------------


class TestRouteAfterTool:
    def test_error_goes_to_repair(self):
        s = empty_state("x")
        s["tool_error"] = "boom"
        assert route_after_tool(s) == "repair"

    def test_ok_goes_to_hitl_gate(self):
        s = empty_state("x")
        s["tool_error"] = None
        assert route_after_tool(s) == "hitl_gate"


# ---- route_after_repair ---------------------------------------------------


class TestRouteAfterRepair:
    def test_error_still_present_gives_up(self):
        s = empty_state("x")
        s["tool_error"] = "still broken"
        assert route_after_repair(s) == "responder"

    def test_error_cleared_retries(self):
        s = empty_state("x")
        s["tool_error"] = None
        assert route_after_repair(s) == "tool_runner"


# ---- route_after_hitl -----------------------------------------------------


class TestRouteAfterHitl:
    def test_reject_goes_to_responder(self):
        s = empty_state("x")
        s["approval_decision"] = "reject"
        s["plan"] = [{"server": "db", "name": "db.execute"}]
        assert route_after_hitl(s) == "responder"

    def test_approve_with_more_steps_goes_to_tool(self):
        s = empty_state("x")
        s["approval_decision"] = "approve"
        s["plan"] = [
            {"server": "db", "name": "db.execute"},
            {"server": "db", "name": "db.query"},
        ]
        s["current_step_index"] = 1  # advance already happened
        assert route_after_hitl(s) == "tool_runner"

    def test_approve_last_step_goes_to_responder(self):
        s = empty_state("x")
        s["approval_decision"] = "approve"
        s["plan"] = [{"server": "db", "name": "db.execute"}]
        s["current_step_index"] = 1
        assert route_after_hitl(s) == "responder"
