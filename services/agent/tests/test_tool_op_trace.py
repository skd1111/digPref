"""工具操作全量打印进思维链测试（2026-08-17）。

背景：此前工具循环一次节点执行只留一条聚合 trace（calls=N），read / write /
glob / grep 等具体操作在思维链里不可见（用户反馈"思考中有点单调"）。

覆盖：
    - _tool_op_trace：per-tool 条目格式（工具名 + 参数摘要 + 成功/失败）
    - 提示词协议路径：每个执行的工具都产出 TOOL_CALL trace 条目
    - native 路径：同上（_emit 统一带上）
    - collector.build_thinking：持久化思维链思考文本枚举每个操作
    - stream._convert_chunk：新增 trace 条目全部增量下发（不再只发最后一条）
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import empty_state, record_trace
from agent.tools.loop import DynamicToolLoop, _tool_op_trace

# ---- 伪对象 ------------------------------------------------------------------


class _ScriptedLoopLLM:
    def __init__(self, actions: list) -> None:
        self._actions = list(actions)

    async def orchestrate_tools(self, **kwargs: Any) -> dict:
        if not self._actions:
            raise AssertionError("script exhausted")
        return self._actions.pop(0)


class _FakeCatalog:
    def __init__(self, results: dict | None = None, defs: list[dict] | None = None) -> None:
        self._results = results or {}
        self._defs = defs if defs is not None else []

    async def summaries(self) -> list[dict]:
        return []

    async def definitions(self, names: list[str] | None = None) -> list[dict]:
        if names is None:
            return self._defs
        return [d for d in self._defs if d.get("name") in names]

    async def execute(self, name: str, args: dict, state: dict) -> dict:
        return self._results.get(name, {"name": name, "ok": True, "result": "ok"})


class _NativeBackend:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)

    async def chat_with_tools(self, messages: list[dict], defs: list[dict]) -> dict:
        if not self._responses:
            raise AssertionError("script exhausted")
        return self._responses.pop(0)


class _NativeLLM:
    def __init__(self, backend: _NativeBackend) -> None:
        self._backend = backend

    async def resolve_native_backend(self) -> tuple[str, Any]:
        return ("cloud", self._backend)


def _action(kind: str, **extra: Any) -> dict:
    base: dict[str, Any] = {
        "action": kind,
        "reason": "test",
        "confidence": 0.9,
        "selected_tool_names": [],
        "desired_capabilities": [],
        "missing_capability": "",
        "tool_calls": [],
        "final_answer": "",
        "ask_user_message": "",
        "need_full_toolset": False,
    }
    base.update(extra)
    return base


def _loop_state(**patch: Any) -> dict:
    st = empty_state("查一下代码")
    st.update(patch)
    return st


def _registered(*names: str) -> list[dict]:
    return [{"name": n, "description": n, "parameters": {}} for n in names]


# ---- _tool_op_trace -----------------------------------------------------------


class TestToolOpTrace:
    def test_success_summary(self):
        entry = _tool_op_trace("read_file", {"path": "d:/a.py"}, {"ok": True})
        assert entry["action"] == "TOOL_CALL"
        assert entry["tool"] == "read_file"
        assert entry["status"] == "ok"
        assert "read_file" in entry["summary"]
        assert "path=d:/a.py" in entry["summary"]
        assert "成功" in entry["summary"]

    def test_failure_summary(self):
        entry = _tool_op_trace("grep", {"pattern": "x"}, {"ok": False, "error": "not_found"})
        assert entry["status"] == "fail"
        assert "失败" in entry["summary"] and "not_found" in entry["summary"]

    def test_long_args_truncated(self):
        entry = _tool_op_trace("write_file", {"content": "x" * 500}, {"ok": True})
        assert len(entry["summary"]) < 300
        assert "…" in entry["summary"]

    def test_many_args_capped(self):
        args = {f"k{i}": i for i in range(8)}
        entry = _tool_op_trace("t", args, {"ok": True})
        assert "…" in entry["summary"]


# ---- 提示词协议路径 -------------------------------------------------------------


class TestPromptProtocolOpTrace:
    async def test_every_tool_call_emits_trace_entry(self):
        script = _action(
            "TOOL_CALLS",
            tool_calls=[
                {"id": "call_1", "name": "read_file", "arguments": {"path": "d:/a.py"}},
                {"id": "call_2", "name": "grep", "arguments": {"pattern": "TODO"}},
                {"id": "call_3", "name": "glob", "arguments": {"pattern": "**/*.py"}},
            ],
        )
        final = _action("FINAL_ANSWER", final_answer="完成。")
        llm = _ScriptedLoopLLM([script, final])
        loop = DynamicToolLoop(llm, _FakeCatalog())  # type: ignore[arg-type]
        st = _loop_state(registered_tools=_registered("read_file", "grep", "glob"))
        out = await loop.run(st)

        tool_entries = [e for e in out["trace"] if e.get("action") == "TOOL_CALL"]
        assert [e["tool"] for e in tool_entries] == ["read_file", "grep", "glob"]
        assert all(e.get("summary") for e in tool_entries)
        # 聚合条目仍在（思维链既有展示不回归）
        assert any(e.get("action") == "TOOL_CALLS" for e in out["trace"])

    async def test_failed_tool_marked_fail(self):
        script = _action(
            "TOOL_CALLS",
            tool_calls=[{"id": "c1", "name": "read_file", "arguments": {"path": "/nope"}}],
        )
        llm = _ScriptedLoopLLM([script])
        catalog = _FakeCatalog(
            results={"read_file": {"name": "read_file", "ok": False, "error": "not_found"}}
        )
        loop = DynamicToolLoop(llm, catalog)  # type: ignore[arg-type]
        st = _loop_state(registered_tools=_registered("read_file"))
        out = await loop.run(st)
        entries = [e for e in out["trace"] if e.get("action") == "TOOL_CALL"]
        assert len(entries) == 1
        assert entries[0]["status"] == "fail"
        assert "not_found" in entries[0]["summary"]


# ---- native 路径 ----------------------------------------------------------------


class TestNativeOpTrace:
    async def test_executed_tools_emitted_via_emit(self):
        backend = _NativeBackend(
            [
                {
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "name": "read_file", "arguments": {"path": "d:/a.py"}},
                        {"id": "c2", "name": "grep", "arguments": {"pattern": "TODO"}},
                    ],
                },
                {"content": "完成。", "tool_calls": []},
            ]
        )
        loop = DynamicToolLoop(
            _NativeLLM(backend), _FakeCatalog(defs=_registered("read_file", "grep"))
        )  # type: ignore[arg-type]
        # full_toolset_loaded=True → 首轮即全量注册，read_file/grep 可调用
        st = _loop_state(tool_calling_mode="native", full_toolset_loaded=True)
        out = await loop.run(st)
        tool_entries = [e for e in out["trace"] if e.get("action") == "TOOL_CALL"]
        assert [e["tool"] for e in tool_entries] == ["read_file", "grep"]
        assert out["final_answer"] == "完成。"


# ---- collector 持久化思维链 -------------------------------------------------------


class TestCollectorBuildThinking:
    def test_tool_ops_enumerated_in_thinking(self):
        from agent.trace.collector import build_thinking

        delta = {
            "trace": [
                _tool_op_trace("read_file", {"path": "d:/a.py"}, {"ok": True}),
                _tool_op_trace("grep", {"pattern": "x"}, {"ok": False, "error": "nf"}),
                record_trace("tool_orchestrator", "ok", action="TOOL_CALLS", calls=2),
            ]
        }
        thinking, _ = build_thinking("tool_orchestrator", delta)
        assert thinking is not None
        assert "read_file" in thinking and "grep" in thinking
        assert "【行动】调用工具 read_file" in thinking
        assert "【观察】调用工具 grep" in thinking  # 失败用【观察】

    def test_legacy_delta_still_renders(self):
        from agent.trace.collector import build_thinking

        thinking, _decision = build_thinking(
            "tool_orchestrator", {"pending_tool_call": {"name": "db.query", "args": {}}}
        )
        assert thinking is not None  # 旧链路不受影响


# ---- stream 增量下发 ---------------------------------------------------------------


class TestStreamTraceIncremental:
    def test_all_new_entries_emitted(self):
        from agent.graph.stream import _convert_chunk

        sent: list[int] = [1]  # 初始已有一条
        snap1 = {"trace": [record_trace("intent", "running"), record_trace("intent", "ok")]}
        events = _convert_chunk("values", snap1, "run-1", set(), sent)
        trace_events = [e for e in events if e["event"] == "trace"]
        assert len(trace_events) == 1  # 只发新增的一条
        assert trace_events[0]["data"]["step"]["status"] == "ok"
        assert sent[0] == 2

        # 工具循环一次产出 3 条新条目 → 全部下发
        snap2 = {
            "trace": snap1["trace"]
            + [
                record_trace("tool_orchestrator", "ok", action="TOOL_CALL", summary="read_file"),
                record_trace("tool_orchestrator", "ok", action="TOOL_CALL", summary="grep"),
                record_trace("tool_orchestrator", "ok", action="TOOL_CALLS", calls=2),
            ]
        }
        events2 = _convert_chunk("values", snap2, "run-1", set(), sent)
        trace_events2 = [e for e in events2 if e["event"] == "trace"]
        assert len(trace_events2) == 3
        assert sent[0] == 5

    def test_no_counter_falls_back_to_last_only(self):
        from agent.graph.stream import _convert_chunk

        snap = {
            "trace": [
                record_trace("intent", "running"),
                record_trace("intent", "ok"),
            ]
        }
        events = _convert_chunk("values", snap, "run-1", set(), None)
        trace_events = [e for e in events if e["event"] == "trace"]
        assert len(trace_events) == 1
        assert trace_events[0]["data"]["step"]["status"] == "ok"
