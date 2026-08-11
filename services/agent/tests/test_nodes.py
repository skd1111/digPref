"""Tests for individual graph nodes."""

from __future__ import annotations

import pytest
from agent.graph.nodes.intent import intent_node
from agent.graph.nodes.planner import _normalise_step, planner_node
from agent.graph.nodes.repair import repair_node
from agent.graph.nodes.responder import responder_node
from agent.graph.nodes.tool_runner import tool_runner_node
from agent.graph.state import empty_state

# ---- intent --------------------------------------------------------------


class TestIntentNode:
    @pytest.mark.asyncio
    async def test_chitchat_when_no_prompt(self, mock_llm):
        s = empty_state("")
        s["messages"] = []
        out = await intent_node(s, mock_llm)
        assert out["intent"] == "chitchat"
        assert out["trace"][0]["node"] == "intent"

    @pytest.mark.asyncio
    async def test_classify(self, mock_llm):
        out = await intent_node(empty_state("show me orders"), mock_llm)
        assert out["intent"] == "query"


# ---- planner -------------------------------------------------------------


class TestPlannerNode:
    @pytest.mark.asyncio
    async def test_plan_with_tools(self, mock_llm, mock_mcp):
        out = await planner_node(empty_state("ping"), mock_llm, mock_mcp)
        assert len(out["plan"]) == 1
        assert out["plan"][0]["server"] == "db"
        assert out["plan"][0]["name"] == "db.query"

    @pytest.mark.asyncio
    async def test_no_tools_yields_empty(self, mock_llm):
        class _EmptyMcp:
            async def list_tools(self):
                return []

            async def invoke(self, *a, **kw):
                return {}

        out = await planner_node(empty_state("ping"), mock_llm, _EmptyMcp())
        assert out["plan"] == []


class TestNormaliseStep:
    def test_valid_step(self):
        specs = ({"server": "db", "name": "db.query", "inputSchema": {}},)
        step = {
            "server": "db",
            "name": "db.query",
            "args": {"sql": "SELECT 1"},
            "risk_level": "read",
        }
        out = _normalise_step(step, specs)
        assert out is not None
        assert out["name"] == "db.query"

    def test_unknown_tool_dropped(self):
        specs = ({"server": "db", "name": "db.query", "inputSchema": {}},)
        step = {"server": "db", "name": "db.unknown", "args": {}, "risk_level": "read"}
        assert _normalise_step(step, specs) is None

    def test_missing_name_dropped(self):
        assert _normalise_step({}, ()) is None


# ---- tool_runner ---------------------------------------------------------


class TestToolRunner:
    @pytest.mark.asyncio
    async def test_successful_call(self, mock_mcp):
        s = empty_state("x")
        s["plan"] = [{"server": "db", "name": "db.query", "args": {"sql": "SELECT 1"}}]
        out = await tool_runner_node(s, mock_mcp)
        assert out["tool_error"] is None
        assert out["tool_result"]["ok"] is True
        assert out["current_step_index"] == 1

    @pytest.mark.asyncio
    async def test_error_captured(self, mock_mcp):
        class _FailingMcp:
            async def invoke(self, *a, **kw):
                raise RuntimeError("boom")

        s = empty_state("x")
        s["plan"] = [{"server": "db", "name": "db.query", "args": {"sql": "SELECT 1"}}]
        out = await tool_runner_node(s, _FailingMcp())
        assert out["tool_error"] and "boom" in out["tool_error"]
        assert out["tool_result"] is None

    @pytest.mark.asyncio
    async def test_no_pending_call(self, mock_mcp):
        s = empty_state("x")
        out = await tool_runner_node(s, mock_mcp)
        assert out["tool_result"] is None
        assert out["tool_error"] is None


# ---- repair --------------------------------------------------------------


class TestRepairNode:
    @pytest.mark.asyncio
    async def test_no_error_skipped(self, mock_llm):
        s = empty_state("x")
        out = await repair_node(s, mock_llm)
        assert out["trace"][0]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_recovers_with_fixed_call(self, mock_llm):
        s = empty_state("x")
        s["pending_tool_call"] = {
            "server": "db",
            "name": "db.query",
            "args": {"sql": "SELECT 1"},
            "risk_level": "read",
        }
        s["tool_error"] = "syntax error near SELECT"
        s["plan"] = [s["pending_tool_call"]]
        out = await repair_node(s, mock_llm)
        assert out["tool_error"] is None
        assert out["retry_count"] == 1
        assert out["pending_tool_call"]["args"]["sql"] == "SELECT 2"

    @pytest.mark.asyncio
    async def test_exhausts_after_max_retries(self, mock_llm):
        s = empty_state("x")
        s["pending_tool_call"] = {
            "server": "db",
            "name": "db.query",
            "args": {"sql": "SELECT 1"},
            "risk_level": "read",
        }
        s["tool_error"] = "still broken"
        s["retry_count"] = 2  # already at max
        out = await repair_node(s, mock_llm)
        assert out["tool_error"] == "still broken"
        assert out["trace"][0]["status"] == "fail"


# ---- responder -----------------------------------------------------------


class TestResponderNode:
    @pytest.mark.asyncio
    async def test_chitchat(self, mock_llm):
        s = empty_state("hello")
        s["intent"] = "chitchat"
        out = await responder_node(s, mock_llm)
        assert "EAIDE" in out["final_answer"]
        assert out["trace"][0]["mode"] == "chitchat"

    @pytest.mark.asyncio
    async def test_empty_plan(self, mock_llm):
        s = empty_state("explain X")
        s["intent"] = "query"
        s["plan"] = []
        out = await responder_node(s, mock_llm)
        assert out["trace"][0]["mode"] == "empty_plan"

    @pytest.mark.asyncio
    async def test_with_results_summarises(self, mock_llm):
        s = empty_state("how many?")
        s["intent"] = "query"
        s["plan"] = [{"server": "db", "name": "db.query"}]
        s["tool_result"] = {"ok": True, "columns": ["n"], "rows": [[42]]}
        out = await responder_node(s, mock_llm)
        assert "Mock final answer" in out["final_answer"]
        assert "db" in out["sources"]

    @pytest.mark.asyncio
    async def test_hard_fail_after_reject(self, mock_llm):
        s = empty_state("update x")
        s["intent"] = "mutate"
        s["plan"] = [{"server": "db", "name": "db.execute"}]
        s["pending_tool_call"] = {"server": "db", "name": "db.execute"}
        s["approval_decision"] = "reject"
        out = await responder_node(s, mock_llm)
        assert "拒绝" in out["final_answer"]
        assert out["trace"][0]["mode"] == "hard_fail"
