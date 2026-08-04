"""End-to-end tests for the compiled LangGraph state machine.

Uses mocked LLM + MCP — no network — to exercise:
    - Read-only path (intent → planner → tool_runner → hitl_gate → responder)
    - HITL pause + approve → continue → responder
    - HITL pause + reject → responder surfaces the rejection
    - Auto-Repair: tool error → repair (retry) → success → responder
    - Auto-Repair exhaustion: tool error × N → responder surfaces failure
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.graph.compile import Runtime, compile_graph
from agent.graph.interrupt import _LOCAL_DECISIONS, post_decision
from agent.graph.state import empty_state


# ---- Shared mocks ---------------------------------------------------------

class _ScriptedLLM:
    """An LMRouter stub that returns scripted plans + canned repair + summary."""

    def __init__(self) -> None:
        self.repair_calls = 0
        self.summarise_calls = 0

    async def classify_intent(self, text: str) -> str:
        return "query"

    async def plan(self, *, intent, user_prompt, history, tool_specs):
        return (
            [{"server": "db", "name": "db.query",
              "args": {"sql": "SELECT 1"}, "risk_level": "read",
              "rationale": "test"}],
            "scripted plan",
        )

    async def repair_call(self, *, original, error, history):
        self.repair_calls += 1
        return {**original, "args": {"sql": "SELECT 2"}}

    async def summarise(self, *, intent, user_prompt, plan, results):
        self.summarise_calls += 1
        return "Mock final answer.", ["db"]


class _FlakyMCP:
    """An McpClient stub that fails the first N calls with `db.query`, then ok."""

    def __init__(self, fails: int = 1) -> None:
        self.fails = fails
        self.calls = 0

    async def list_tools(self):
        return [{
            "server": "db",
            "name": "db.query",
            "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}},
        }]

    async def invoke(self, call, *, timeout_sec, row_limit):
        self.calls += 1
        if self.calls <= self.fails:
            raise RuntimeError(f"flaky failure #{self.calls}")
        return {"ok": True, "columns": ["n"], "rows": [[42]], "truncated": False,
                "rows_returned": 1}


class _WritePlanLLM:
    """Planner that returns a write call (so HITL gate triggers)."""

    def __init__(self) -> None:
        self.summarise_calls = 0
        self.repair_calls = 0

    async def classify_intent(self, text):
        return "mutate"

    async def plan(self, *, intent, user_prompt, history, tool_specs):
        return (
            [{"server": "db", "name": "db.execute",
              "args": {"sql": "UPDATE t SET x=1 WHERE id=1", "approval_id": "TEST"},
              "risk_level": "medium", "rationale": "test"}],
            "scripted write plan",
        )

    async def repair_call(self, *, original, error, history):
        self.repair_calls += 1
        return original

    async def summarise(self, *, intent, user_prompt, plan, results):
        self.summarise_calls += 1
        return "Mock final.", []


class _WriteMCP:
    async def list_tools(self):
        return [{"server": "db", "name": "db.execute",
                 "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}}]

    async def invoke(self, call, *, timeout_sec, row_limit):
        return {"ok": True, "rows_affected": 1}


# ---- Build the graph with the same Runtime dataclass used in compile.py --

def _build(llm, mcp):
    return compile_graph(Runtime(llm=llm, mcp=mcp))


# ---- Tests ----------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_local_decisions():
    _LOCAL_DECISIONS.clear()
    yield
    _LOCAL_DECISIONS.clear()


class TestReadOnlyPath:
    @pytest.mark.asyncio
    async def test_full_path_to_done(self):
        llm = _ScriptedLLM()
        mcp = _FlakyMCP(fails=0)
        graph = _build(llm, mcp)
        events = []
        async for evt in graph.astream(
            empty_state("show orders"),
            {"configurable": {"thread_id": "t1"}},
            stream_mode="updates",
        ):
            events.append(evt)
        assert llm.summarise_calls == 1
        # final state should contain the answer
        last_node = list(events[-1].keys())[0]
        assert last_node == "responder"


class TestAutoRepair:
    @pytest.mark.asyncio
    async def test_repairs_then_succeeds(self):
        llm = _ScriptedLLM()
        mcp = _FlakyMCP(fails=1)
        graph = _build(llm, mcp)
        async for _ in graph.astream(
            empty_state("show orders"),
            {"configurable": {"thread_id": "t2"}},
            stream_mode="updates",
        ):
            pass
        assert llm.repair_calls == 1
        assert llm.summarise_calls == 1

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self):
        llm = _ScriptedLLM()
        mcp = _FlakyMCP(fails=10)   # never succeeds
        graph = _build(llm, mcp)
        last_answer = None
        async for chunk in graph.astream(
            empty_state("show orders"),
            {"configurable": {"thread_id": "t3"}},
            stream_mode="updates",
        ):
            for node_name, delta in chunk.items():
                if node_name == "responder" and delta.get("final_answer"):
                    last_answer = delta["final_answer"]
        # Retried at most MAX_RETRIES times (default 2), then gave up
        assert llm.repair_calls == 2
        # Responder surfaced the failure via hard_fail (no LLM.summarise call)
        assert last_answer is not None
        assert "失败" in last_answer or "重试" in last_answer


class TestHitlPath:
    @pytest.mark.asyncio
    async def test_approve_resumes_and_completes(self):
        # Schedule the approval decision BEFORE the graph reaches the gate.
        async def _approve_later():
            await asyncio.sleep(0.2)
            await post_decision("TEST", "approve")

        asyncio.create_task(_approve_later())
        llm = _WritePlanLLM()
        mcp = _WriteMCP()
        graph = _build(llm, mcp)
        async for _ in graph.astream(
            empty_state("update something"),
            {"configurable": {"thread_id": "t4"}},
            stream_mode="updates",
        ):
            pass
        assert llm.summarise_calls == 1

    @pytest.mark.asyncio
    async def test_reject_short_circuits_to_responder(self):
        async def _reject():
            await asyncio.sleep(0.2)
            await post_decision("TEST", "reject")

        asyncio.create_task(_reject())
        llm = _WritePlanLLM()
        mcp = _WriteMCP()
        graph = _build(llm, mcp)
        # After rejection, mcp.invoke should NEVER be called
        async for _ in graph.astream(
            empty_state("update something"),
            {"configurable": {"thread_id": "t5"}},
            stream_mode="updates",
        ):
            pass
        # Responder still synthesises an answer explaining the rejection
        assert llm.summarise_calls == 1