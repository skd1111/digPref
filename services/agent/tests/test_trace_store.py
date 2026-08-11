"""Tests for observability/trace_store.py — SQLite trace persistence."""

from __future__ import annotations

import sqlite3

import pytest
from agent.observability import trace_store
from agent.observability.trace_store import query_run, record


class TestTraceStore:
    @pytest.mark.asyncio
    async def test_record_and_query_run(self, tmp_path, monkeypatch):
        audit_db = tmp_path / "audit.sqlite"
        monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(audit_db))

        run_id = "run_test_001"
        await record("intent", "ok", run_id=run_id, duration_ms=12, summary="intent=query")
        await record(
            "planner",
            "ok",
            run_id=run_id,
            duration_ms=87,
            summary="3 steps",
            rationale="fetch user data",
        )
        await record(
            "tool_runner", "ok", run_id=run_id, duration_ms=250, tool_name="db.query", attempts=1
        )
        await record("responder", "ok", run_id=run_id, duration_ms=400)

        rows = await query_run(run_id)
        assert [r["node"] for r in rows] == ["intent", "planner", "tool_runner", "responder"]
        assert rows[0]["duration_ms"] == 12
        assert rows[2]["tool_name"] == "db.query"
        assert rows[1]["rationale"] == "fetch user data"

    @pytest.mark.asyncio
    async def test_failed_step_records_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))
        run_id = "run_test_002"
        await record("tool_runner", "fail", run_id=run_id, error="RuntimeError: connection refused")
        rows = await query_run(run_id)
        assert rows[0]["status"] == "fail"
        assert "connection refused" in rows[0]["error"]

    @pytest.mark.asyncio
    async def test_hitl_step_records_approval_id_and_decision(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))
        run_id = "run_test_003"
        await record(
            "hitl_gate",
            "ok",
            run_id=run_id,
            approval_id="appr_abc",
            decision="approve",
            duration_ms=2000,
        )
        rows = await query_run(run_id)
        assert rows[0]["approval_id"] == "appr_abc"
        assert rows[0]["decision"] == "approve"

    @pytest.mark.asyncio
    async def test_unrelated_runs_are_separated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))
        await record("intent", "ok", run_id="run_A", duration_ms=10)
        await record("intent", "ok", run_id="run_B", duration_ms=20)

        a = await query_run("run_A")
        b = await query_run("run_B")
        assert len(a) == 1 and a[0]["duration_ms"] == 10
        assert len(b) == 1 and b[0]["duration_ms"] == 20

    @pytest.mark.asyncio
    async def test_persistence_survives_reopen(self, tmp_path, monkeypatch):
        """Recording rows, closing, reopening → rows still queryable."""
        monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))
        await record("intent", "ok", run_id="run_persist", duration_ms=5)
        # Force a fresh connection by querying again — the file is on disk now
        rows = await query_run("run_persist")
        assert len(rows) == 1

        # Verify the row is actually persisted (not just in-memory)
        conn = sqlite3.connect(str(tmp_path / "audit.sqlite"))
        count = conn.execute("SELECT COUNT(*) FROM trace").fetchone()[0]
        conn.close()
        assert count == 1


class TestStreamAllRuns:
    @pytest.mark.asyncio
    async def test_groups_by_run_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))
        for run_id in ("alpha", "beta"):
            for node in ("intent", "planner", "responder"):
                await record(node, "ok", run_id=run_id, duration_ms=10)

        runs = [r async for r in trace_store.stream_all_runs()]
        by_id = {r["run_id"]: r for r in runs}
        assert by_id["alpha"]["step_count"] == 3
        assert by_id["beta"]["step_count"] == 3


class TestLangSmithExport:
    @pytest.mark.asyncio
    async def test_export_skipped_without_api_key(self, tmp_path, monkeypatch):
        """Without LANGCHAIN_API_KEY, export is a no-op — must not raise."""
        monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))
        monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
        # Should not raise
        await record("intent", "ok", run_id="run_no_ls", duration_ms=1)
        assert True

    @pytest.mark.asyncio
    async def test_export_makes_post_call(self, tmp_path, monkeypatch):
        """With API key set, the exporter POSTs to LangSmith (we mock httpx)."""
        monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))
        monkeypatch.setenv("LANGCHAIN_API_KEY", "test-key")
        monkeypatch.setenv("LANGCHAIN_ENDPOINT", "https://example.test/v1")

        posted = []
        import httpx

        class _MockAsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def post(self, url, **kw):
                posted.append((url, kw.get("json")))
                return httpx.Response(200)

        monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
        # The _export_to_langsmith is referenced via `import httpx` inside the function,
        # so we patch the module-level symbol.
        # Direct test of the export function
        await trace_store._export_to_langsmith(
            {
                "run_id": "r1",
                "node": "intent",
                "status": "ok",
                "ts": "2026-07-01T00:00:00",
                "duration_ms": 10,
            }
        )
        assert posted, "expected LangSmith POST"
        assert posted[0][0].endswith("/runs")
        assert posted[0][1]["name"] == "intent"
