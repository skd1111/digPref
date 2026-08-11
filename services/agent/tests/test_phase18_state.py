"""Phase 18 AgentState 字段默认值契约。"""

from __future__ import annotations

from agent.graph.state import empty_state


def test_empty_state_phase18_defaults():
    s = empty_state("hi")
    assert s["work_mode"] == "full"
    assert s["autonomy"] == "interactive"
    assert s["routing"] is None
    assert s["routing_overridden"] is False
    assert s["routing_declaration"] is None
    assert s["execution_policies"] == []
    assert s["error_feedback"] == []
    assert s["repair_attempt"] == 0
