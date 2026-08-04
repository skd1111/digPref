"""Phase 16 · 思维链可视化与文件操作追踪测试。

覆盖：
    - diff：unified diff 计算 / 行数统计 / 预览截断
    - models：ThinkingStep / FileOperation 序列化往返
    - storage：thinking_steps 插入 / 查询 / 计数
    - collector：中文思维链构建 / 文件操作提取与挂载
    - api：/trace 三端点
"""
from __future__ import annotations

import pytest

from agent.trace import storage
from agent.trace.collector import (
    TraceCollector,
    build_thinking,
    extract_file_operation,
    extract_tool_calls,
)
from agent.trace.diff import (
    build_diff_fields,
    compute_unified_diff,
    diff_stats,
    extract_preview,
)
from agent.trace.models import FileOperation, ThinkingStep


# ---- diff ------------------------------------------------------------------

class TestDiff:
    def test_unified_diff_basic(self):
        diff = compute_unified_diff("a\nb\nc\n", "a\nB\nc\n", "x.py")
        assert "-b" in diff
        assert "+B" in diff
        assert "a/x.py" in diff and "b/x.py" in diff

    def test_diff_stats(self):
        diff = compute_unified_diff("a\nb\n", "a\nc\nd\n", "f")
        added, removed = diff_stats(diff)
        assert added == 2 and removed == 1

    def test_new_file_diff(self):
        diff = compute_unified_diff("", "line1\nline2\n", "new.py")
        added, removed = diff_stats(diff)
        assert added == 2 and removed == 0

    def test_no_change_empty_diff(self):
        assert compute_unified_diff("same\n", "same\n", "f") == ""

    def test_preview_truncation(self):
        diff = "\n".join(f"+line{i}" for i in range(300))
        preview = extract_preview(diff, max_lines=100)
        assert len(preview.splitlines()) == 101  # 100 行 + 截断提示
        assert "已截断" in preview

    def test_build_diff_fields(self):
        fields = build_diff_fields("a\n", "a\nb\n", "f.txt")
        assert fields["lines_added"] == 1
        assert fields["lines_removed"] == 0
        assert "+b" in fields["diff"]


# ---- models ----------------------------------------------------------------

class TestModels:
    def test_file_operation_roundtrip(self):
        op = FileOperation(type="edit", path="/a.py", diff="+x", preview="+x",
                           lines_added=1, lines_removed=0, start_line=1, end_line=9)
        back = FileOperation.from_dict(op.to_dict())
        assert back.type == "edit" and back.path == "/a.py"
        assert back.lines_added == 1 and back.end_line == 9

    def test_thinking_step_roundtrip(self):
        step = ThinkingStep(
            session_id="s1", node_name="planner", step_index=2,
            thinking="【思考】分析平账差异", decision="执行模式：TOOL_ONLY",
            tool_calls=[{"name": "read_file"}],
            file_operations=[FileOperation(type="read", path="/x.py")],
        )
        back = ThinkingStep.from_dict(step.to_dict())
        assert back.session_id == "s1"
        assert back.node_name == "planner"
        assert back.step_index == 2
        assert back.file_operations[0].path == "/x.py"


# ---- storage ---------------------------------------------------------------

class TestStorage:
    @pytest.mark.asyncio
    async def test_insert_and_list(self):
        step = ThinkingStep(session_id="sess-a", node_name="intent",
                            step_index=0, thinking="【思考】识别意图")
        await storage.insert_step(step)
        rows = await storage.list_steps("sess-a")
        assert len(rows) == 1
        assert rows[0].thinking == "【思考】识别意图"

    @pytest.mark.asyncio
    async def test_insert_idempotent(self):
        step = ThinkingStep(session_id="sess-b", node_name="intent", step_index=0)
        await storage.insert_step(step)
        await storage.insert_step(step)  # 同 id 重复插入忽略
        assert await storage.count_steps("sess-b") == 1

    @pytest.mark.asyncio
    async def test_get_step_and_missing(self):
        step = ThinkingStep(session_id="sess-c", node_name="responder", step_index=1)
        await storage.insert_step(step)
        got = await storage.get_step(step.id)
        assert got is not None and got.node_name == "responder"
        assert await storage.get_step("no-such-id") is None

    @pytest.mark.asyncio
    async def test_session_isolation(self):
        await storage.insert_step(ThinkingStep(session_id="s1", node_name="intent", step_index=0))
        await storage.insert_step(ThinkingStep(session_id="s2", node_name="intent", step_index=0))
        assert len(await storage.list_steps("s1")) == 1
        assert len(await storage.list_steps("s2")) == 1


# ---- collector -------------------------------------------------------------

class TestCollector:
    def test_build_thinking_planner_chinese(self):
        delta = {
            "plan_explanation": "需要查询订单表核对借贷差额",
            "plan": [{"server": "db", "name": "db.query"}],
        }
        thinking, decision = build_thinking("planner", delta)
        assert thinking is not None
        assert "【思考】" in thinking and "【行动】" in thinking
        assert "查询订单表" in thinking

    def test_build_thinking_responder_decision(self):
        thinking, decision = build_thinking("responder", {"final_answer": "平账完成"})
        assert decision == "平账完成"
        assert "【决策】" in thinking

    def test_build_thinking_tool_observation(self):
        delta = {
            "pending_tool_call": {"server": "builtin", "name": "read_file",
                                  "args": {"path": "/a.py"}, "risk_level": "read"},
            "tool_result": {"ok": True, "content": "hello"},
        }
        thinking, _ = build_thinking("tool_runner", delta)
        assert "【行动】" in thinking and "read_file" in thinking
        assert "【观察】" in thinking

    def test_build_thinking_empty_delta(self):
        thinking, decision = build_thinking("intent", {})
        assert thinking is None and decision is None

    def test_extract_tool_calls(self):
        delta = {"pending_tool_call": {"name": "grep", "server": "builtin",
                                       "args": {"pattern": "x"}}}
        calls = extract_tool_calls(delta)
        assert len(calls) == 1 and calls[0]["name"] == "grep"

    def test_extract_file_op_write_with_diff(self):
        op = extract_file_operation(
            "write_file",
            {"path": "/tmp/new.py", "content": "a\nb\n"},
            {"ok": True},
            before="",
            after="a\nb\n",
        )
        assert op is not None and op.type == "write"
        assert op.lines_added == 2 and op.diff and "+a" in op.diff

    def test_extract_file_op_edit(self):
        op = extract_file_operation(
            "edit_file", {"path": "/tmp/e.py"}, {"ok": True},
            before="x = 1\n", after="x = 2\n",
        )
        assert op.type == "edit"
        assert op.lines_added == 1 and op.lines_removed == 1

    def test_extract_file_op_read_line_range(self):
        op = extract_file_operation(
            "read_file", {"path": "/tmp/r.py"},
            {"ok": True, "meta": {"start_line": 10, "line_count": 5}},
        )
        assert op.type == "read" and op.start_line == 10 and op.end_line == 15
        assert op.diff is None

    def test_extract_file_op_non_file_tool(self):
        assert extract_file_operation("calculator", {"a": 1}, {"ok": True}) is None

    @pytest.mark.asyncio
    async def test_record_node_step_persists(self):
        c = TraceCollector()
        step = await c.record_node_step(
            "run-1", "planner",
            {"plan_explanation": "核对借贷方金额", "plan": [{"name": "db.query"}]},
            latency_ms=42,
        )
        assert step is not None and step.step_index == 0
        rows = await storage.list_steps("run-1")
        assert len(rows) == 1 and "核对借贷方金额" in rows[0].thinking

        # 第二个节点 step_index 递增
        step2 = await c.record_node_step("run-1", "responder", {"final_answer": "完成"})
        assert step2.step_index == 1

    @pytest.mark.asyncio
    async def test_record_skips_empty_delta(self):
        c = TraceCollector()
        assert await c.record_node_step("run-2", "intent", {}) is None
        assert await storage.count_steps("run-2") == 0

    @pytest.mark.asyncio
    async def test_attach_file_operation_to_last_step(self):
        c = TraceCollector()
        await c.record_node_step("run-3", "tool_runner", {
            "pending_tool_call": {"name": "edit_file", "server": "builtin",
                                  "args": {"path": "/f.py"}},
        })
        op = FileOperation(type="edit", path="/f.py", diff="+x", lines_added=1)
        ok = await c.attach_file_operation("run-3", op)
        assert ok
        rows = await storage.list_steps("run-3")
        assert len(rows[0].file_operations) == 1
        assert rows[0].file_operations[0].path == "/f.py"

    @pytest.mark.asyncio
    async def test_attach_file_operation_without_step_creates_one(self):
        c = TraceCollector()
        op = FileOperation(type="write", path="/n.py")
        await c.attach_file_operation("run-4", op)
        rows = await storage.list_steps("run-4")
        assert len(rows) == 1 and rows[0].node_name == "builtin_tool"


# ---- api -------------------------------------------------------------------

class TestApi:
    @pytest.mark.asyncio
    async def test_session_endpoint(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from agent.trace import api as trace_api

        c = TraceCollector()
        await c.record_node_step("run-api", "intent", {"intent": "query"})

        app = FastAPI()
        app.include_router(trace_api.router)
        client = TestClient(app)
        resp = client.get("/trace/session/run-api")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "run-api" and body["count"] == 1
        assert body["steps"][0]["node_name"] == "intent"

    @pytest.mark.asyncio
    async def test_step_and_file_diff_endpoints(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from agent.trace import api as trace_api

        step = ThinkingStep(
            session_id="run-api2", node_name="tool_runner", step_index=0,
            file_operations=[
                FileOperation(type="edit", path="/a.py", diff="+new", preview="+new",
                              lines_added=1),
            ],
        )
        await storage.insert_step(step)

        app = FastAPI()
        app.include_router(trace_api.router)
        client = TestClient(app)

        resp = client.get(f"/trace/step/{step.id}")
        assert resp.status_code == 200 and resp.json()["node_name"] == "tool_runner"

        resp = client.get(f"/trace/file-diff/{step.id}/0")
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == "/a.py" and body["diff"] == "+new"
        assert body["lines_added"] == 1

        # 越界 / 不存在 → 404
        assert client.get(f"/trace/file-diff/{step.id}/9").status_code == 404
        assert client.get("/trace/step/missing").status_code == 404
