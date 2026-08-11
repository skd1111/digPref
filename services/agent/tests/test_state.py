"""Tests for graph/state.py — AgentState + helpers."""

from __future__ import annotations

from agent.graph.state import advance, empty_state, next_step, record_trace


class TestEmptyState:
    def test_initial_shape(self):
        s = empty_state("hello")
        assert s["user_prompt"] == "hello"
        assert s["intent"] is None
        assert s["plan"] == []
        assert s["current_step_index"] == 0
        assert s["retry_count"] == 0
        assert s["trace"] == []
        assert s["awaiting_approval"] is False
        assert s["truncated_any"] is False
        assert s["step_started_at"]  # auto-set

    def test_messages_includes_user(self):
        s = empty_state("ping")
        assert s["messages"][0]["role"] == "user"
        assert s["messages"][0]["content"] == "ping"


class TestNextStep:
    def test_returns_first_when_at_start(self):
        s = empty_state("x")
        s["plan"] = [{"server": "db", "name": "db.query"}]
        assert next_step(s) == {"server": "db", "name": "db.query"}

    def test_returns_none_when_done(self):
        s = empty_state("x")
        s["plan"] = [{"server": "db", "name": "db.query"}]
        s["current_step_index"] = 1
        assert next_step(s) is None

    def test_returns_none_when_plan_empty(self):
        s = empty_state("x")
        assert next_step(s) is None


class TestAdvance:
    def test_increments_index(self):
        s = empty_state("x")
        s["current_step_index"] = 2
        delta = advance(s)
        assert delta["current_step_index"] == 3


class TestRecordTrace:
    def test_basic_shape(self):
        e = record_trace("intent", "ok", intent="query")
        assert e["node"] == "intent"
        assert e["status"] == "ok"
        assert e["intent"] == "query"
        assert "ts" in e

    def test_extra_kwargs_merged(self):
        e = record_trace("tool_runner", "fail", error="boom", attempt=2)
        assert e["error"] == "boom"
        assert e["attempt"] == 2
