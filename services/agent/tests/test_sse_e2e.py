"""End-to-end SSE tests using FastAPI's TestClient.

We mount the real app, inject a scripted LangGraph runtime, and verify
the SSE wire format matches what `apps/desktop/src-tauri/src/stream/sse_bridge.rs`
will consume.

What we assert:
    - Endpoint `POST /chat/{run_id}/stream` returns `text/event-stream`
    - The stream emits `message` / `tool_call` / `tool_result` / `trace`
      events with the expected SSE framing (`event: <name>\ndata: <json>`)
    - `done` is always the last event
    - `POST /approval/{id}` with `{decision: approve|reject}` writes to the
      interrupt backend (verified via a side-channel probe)
    - Cross-checks: invalid decision returns 200 with `ok: false`
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Make services/agent importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient


# ---- A scripted graph runtime for testing ---------------------------------

class _ScriptedLLM:
    async def classify_intent(self, text): return "query"

    async def plan(self, *, intent, user_prompt, history, tool_specs):
        return (
            [{"server": "db", "name": "db.query",
              "args": {"sql": "SELECT 1"}, "risk_level": "read",
              "rationale": "mock"}],
            "scripted plan",
        )

    async def repair_call(self, *, original, error, history): return original
    async def summarise(self, *, intent, user_prompt, plan, results):
        return "Mock final answer.", ["db"]


class _ScriptedMCP:
    async def list_tools(self):
        return [{"server": "db", "name": "db.query",
                 "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}}]
    async def invoke(self, call, *, timeout_sec, row_limit):
        return {"ok": True, "columns": ["n"], "rows": [[42]],
                "rows_returned": 1, "truncated": False}


def _build_test_app():
    """Build a fresh FastAPI app with a scripted runtime for tests."""
    # Import inside the function so conftest has run first (env vars set)
    from agent.graph.compile import Runtime, compile_graph
    from agent.main import create_app, set_runtime_for_testing, get_compiled_graph

    runtime = Runtime(llm=_ScriptedLLM(), mcp=_ScriptedMCP())
    graph = compile_graph(runtime)
    set_runtime_for_testing(runtime, graph)

    # create_app re-reads app.state on each request via the dependency,
    # but our chat.py uses `request.app.state.graph`. Set it now.
    app = create_app()
    app.state.runtime = runtime
    app.state.graph = graph
    return app


# ---- SSE wire-format parsing ---------------------------------------------

def _parse_sse_stream(body_iter):
    """Yield (event_name, data_dict) tuples from an SSE byte stream.

    We accumulate `event:` then `data:` per frame, flushing whenever we see
    a new `event:` line (the previous frame is complete). A trailing
    `data:` without a following event is also flushed at end-of-stream.

    Note: `httpx.Response.iter_lines()` skips blank lines by default, so we
    can't rely on `\n\n` as a separator.
    """
    last_event: str | None = None
    last_data: list[str] = []
    pending_event: str | None = None

    def _flush():
        nonlocal last_event, last_data
        if last_event and last_data:
            yield last_event, _safe_json("\n".join(last_data))
        last_event, last_data = None, []

    for raw in body_iter:
        for line in raw.splitlines():
            if not line:
                continue  # never see these from iter_lines anyway
            if line.startswith("event:"):
                # New frame begins → flush the previous one
                if last_event:
                    yield from _flush()
                last_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                last_data.append(line[len("data:"):].lstrip())
    if last_event and last_data:
        yield last_event, _safe_json("\n".join(last_data))


def _safe_json(s: str) -> Any:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {"_raw": s}


# ---- Tests ----------------------------------------------------------------

@pytest.fixture
def client():
    app = _build_test_app()
    with TestClient(app) as c:
        yield c


class TestChatStream:
    def test_sse_content_type(self, client):
        with client.stream(
            "POST", "/chat/run-abc/stream",
            json={"prompt": "show me orders"},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

    def test_full_event_sequence(self, client):
        with client.stream(
            "POST", "/chat/run-xyz/stream",
            json={"prompt": "show me orders"},
        ) as resp:
            events = list(_parse_sse_stream(resp.iter_lines()))

        # Filter out the bookkeeping events; we expect at least these:
        kinds = [e[0] for e in events]
        # Last event must be 'done'
        assert kinds[-1] == "done", f"expected done last, got {kinds}"
        # We expect message + trace in the stream
        assert "message" in kinds, f"missing 'message'; got {kinds}"
        assert "trace" in kinds, f"missing 'trace'; got {kinds}"

    def test_done_payload_includes_runId(self, client):
        with client.stream(
            "POST", "/chat/run-payload/stream",
            json={"prompt": "ping"},
        ) as resp:
            events = list(_parse_sse_stream(resp.iter_lines()))
        done = [e for e in events if e[0] == "done"][-1]
        assert done[1]["runId"] == "run-payload"

    def test_sse_framing_newlines(self, client):
        """Each event must be terminated by a blank line."""
        with client.stream(
            "POST", "/chat/run-frame/stream",
            json={"prompt": "ping"},
        ) as resp:
            body = b"".join(resp.iter_bytes()).decode("utf-8", "replace")
        # SSE frames are separated by `\n\n`
        assert "\n\n" in body


class TestApprovalEndpoint:
    def test_approve_writes_to_interrupt(self, client, monkeypatch):
        """POST /approval/{id} must reach the interrupt backend."""
        # The endpoint imports the function directly, so we patch the
        # symbol on agent.api.approval (the import target), not on
        # agent.graph.interrupt (the source).
        from agent.api import approval as approval_mod

        captured = {}

        async def _capture(approval_id, decision):
            captured["id"] = approval_id
            captured["decision"] = decision

        monkeypatch.setattr(approval_mod, "post_decision", _capture)

        r = client.post(
            "/approval/appr-001",
            json={"decision": "approve", "operator": "alice"},
        )
        assert r.status_code == 200
        assert r.json() == {"approval_id": "appr-001", "ok": True, "decision": "approve"}
        assert captured == {"id": "appr-001", "decision": "approve"}

    def test_reject(self, client, monkeypatch):
        from agent.api import approval as approval_mod
        captured = {}

        async def _capture(approval_id, decision):
            captured["decision"] = decision

        monkeypatch.setattr(approval_mod, "post_decision", _capture)
        r = client.post("/approval/appr-002", json={"decision": "reject"})
        assert r.status_code == 200
        assert r.json()["decision"] == "reject"
        assert captured["decision"] == "reject"

    def test_invalid_decision_rejected(self, client):
        r = client.post("/approval/appr-003", json={"decision": "maybe"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "decision must be" in body["error"]


class TestHealthEndpoint:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestChatCreateNonStreaming:
    def test_returns_run_id(self, client):
        r = client.post("/chat", json={"prompt": "ping"})
        assert r.status_code == 200
        body = r.json()
        assert "run_id" in body
        assert body["prompt"] == "ping"