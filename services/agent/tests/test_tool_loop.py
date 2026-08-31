"""动态工具加载与工具调用模块测试（2026-08-03）。

覆盖：
    - ToolCatalog：摘要 / 定义 / 执行（builtin + MCP）
    - DynamicToolLoop：五动作状态机、HITL 暂停-批准/拒绝-恢复、轮次上限、违规兜底
    - LMRouter：动作 JSON 严格解析 / 提示词占位符填充
    - 新增常用工具（datetime / uuid / http / csv / text_split）与 Rust 工具 Python 兜底
    - 图级 e2e：decompose(TOOL_ONLY) → 循环 → responder
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent.builtin.extra import (
    builtin_csv_parse,
    builtin_datetime_now,
    builtin_http_get,
    builtin_text_split,
    builtin_uuid4,
)
from agent.builtin.fallbacks import (
    builtin_base64_py,
    builtin_find_py,
    builtin_glob_py,
    builtin_hash_py,
    builtin_stat_file_py,
)
from agent.config import settings
from agent.graph.compile import Runtime, compile_graph
from agent.graph.edges import route_after_tool_loop
from agent.graph.state import empty_state
from agent.llm.router import LMRouter, _parse_orchestration_action
from agent.tools.catalog import ToolCatalog
from agent.tools.loop import DynamicToolLoop

# ---- 伪对象 ----------------------------------------------------------------


class _ScriptedLoopLLM:
    """orchestrate_tools 按剧本返回动作；支持抛异常模拟 LLM 故障。"""

    def __init__(self, actions: list) -> None:
        self._actions = list(actions)
        self.calls: list[dict] = []

    async def orchestrate_tools(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        if not self._actions:
            raise AssertionError("script exhausted")
        item = self._actions.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeCatalog:
    """内存目录：固定摘要 / 定义 / 执行结果。"""

    def __init__(
        self,
        *,
        names: list[str] | None = None,
        execute_results: dict | None = None,
    ) -> None:
        names = names or ["get_weather", "write_file"]
        self._summaries = [
            {"name": n, "description": f"desc {n}", "category": "builtin", "keywords": []}
            for n in names
        ]
        self._defs = [
            {
                "name": n,
                "description": f"desc {n}",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "server": "builtin",
                "risk": "write" if "write" in n else "read",
            }
            for n in names
        ]
        self._execute_results = execute_results or {}
        self.executed: list[tuple[str, dict]] = []
        self.definition_calls: list[Any] = []

    async def summaries(self) -> list[dict]:
        return self._summaries

    async def definitions(self, names: list[str] | None = None) -> list[dict]:
        self.definition_calls.append(names)
        if names is None:
            return self._defs
        return [d for d in self._defs if d["name"] in names]

    async def execute(self, name: str, args: dict, state: dict) -> dict:
        self.executed.append((name, args))
        return self._execute_results.get(
            name,
            {"name": name, "ok": True, "result": f"result:{name}"},
        )


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
    st = empty_state("查一下天气")
    st.update(patch)
    return st


# ---- ToolCatalog -----------------------------------------------------------


class TestToolCatalog:
    async def test_summaries_include_builtin_and_mcp(self):
        class _MCP:
            async def list_tools(self):
                return [
                    {"server": "db", "name": "db.query", "description": "查询数据库"},
                ]

        catalog = ToolCatalog(mcp=_MCP())
        summaries = await catalog.summaries()
        names = {s["name"] for s in summaries}
        assert "read_file" in names  # builtin
        assert "db.db.query" in names  # mcp（server.name 全名）
        assert all("name" in s and "description" in s for s in summaries)

    async def test_definitions_filter_and_full(self):
        catalog = ToolCatalog(mcp=None)
        few = await catalog.definitions(["calculator", "read_file"])
        assert {d["name"] for d in few} == {"calculator", "read_file"}
        assert all("parameters" in d and d["server"] == "builtin" for d in few)
        all_defs = await catalog.definitions()
        assert "read_file" in {d["name"] for d in all_defs}
        assert len(all_defs) >= 24  # 19 既有 + 5 新增

    async def test_execute_builtin_calculator(self):
        catalog = ToolCatalog(mcp=None)
        result = await catalog.execute("calculator", {"expression": "1 + 2"}, {})
        assert result["ok"] is True
        assert result["result"]["value"] == 3

    async def test_execute_unknown_tool(self):
        catalog = ToolCatalog(mcp=None)
        result = await catalog.execute("no_such_tool", {}, {})
        assert result["ok"] is False


# ---- DynamicToolLoop -------------------------------------------------------


class TestDynamicToolLoop:
    async def test_select_then_call_then_final(self):
        catalog = _FakeCatalog()
        llm = _ScriptedLoopLLM(
            [
                _action("SELECT_TOOLS", selected_tool_names=["get_weather"]),
                _action(
                    "TOOL_CALLS",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "name": "get_weather",
                            "arguments": {"city": "北京"},
                            "purpose": "查天气",
                        }
                    ],
                ),
                _action("FINAL_ANSWER", final_answer="北京今天晴。"),
            ]
        )
        loop = DynamicToolLoop(llm, catalog)
        out = await loop.run(_loop_state())
        assert out["load_stage"] == "CANDIDATE_REGISTERED"
        assert out["tool_results"] == []

        out = await loop.run(_loop_state(**out))
        assert out["tool_results"][0]["name"] == "get_weather"
        assert catalog.executed == [("get_weather", {"city": "北京"})]

        out = await loop.run(_loop_state(**out))
        assert out["final_answer"] == "北京今天晴。"
        assert out["tool_loop_active"] is False

    async def test_request_full_tools_then_call(self):
        catalog = _FakeCatalog()
        llm = _ScriptedLoopLLM(
            [
                _action("REQUEST_FULL_TOOLS", missing_capability="缺数据库"),
                _action(
                    "TOOL_CALLS",
                    tool_calls=[{"id": "c1", "name": "get_weather", "arguments": {}}],
                ),
                _action("FINAL_ANSWER", final_answer="done"),
            ]
        )
        loop = DynamicToolLoop(llm, catalog)
        out = await loop.run(_loop_state())
        assert out["load_stage"] == "FULL_REGISTERED"
        assert out["full_toolset_loaded"] is True
        out = await loop.run(_loop_state(**out))
        assert out["tool_results"][0]["name"] == "get_weather"
        out = await loop.run(_loop_state(**out))
        assert out["final_answer"] == "done"

    async def test_ask_user_returns_message(self):
        catalog = _FakeCatalog()
        llm = _ScriptedLoopLLM(
            [
                _action("ASK_USER", ask_user_message="请问查哪个城市？"),
            ]
        )
        loop = DynamicToolLoop(llm, catalog)
        out = await loop.run(_loop_state())
        assert out["final_answer"] == "请问查哪个城市？"

    async def test_final_answer_direct(self):
        catalog = _FakeCatalog()
        llm = _ScriptedLoopLLM([_action("FINAL_ANSWER", final_answer="直接回答")])
        loop = DynamicToolLoop(llm, catalog)
        out = await loop.run(_loop_state())
        assert out["final_answer"] == "直接回答"
        assert catalog.executed == []

    async def test_unregistered_call_rejected_without_execution(self):
        catalog = _FakeCatalog()
        llm = _ScriptedLoopLLM(
            [
                _action(
                    "TOOL_CALLS",
                    tool_calls=[{"id": "c1", "name": "not_registered", "arguments": {}}],
                ),
                _action("FINAL_ANSWER", final_answer="done"),
            ]
        )
        loop = DynamicToolLoop(llm, catalog)
        out = await loop.run(_loop_state())
        assert out["tool_results"][0]["error"] == "unregistered_tool"
        assert catalog.executed == []
        out = await loop.run(_loop_state(**out))
        assert out["final_answer"] == "done"

    async def test_hitl_pause_approve_resume(self):
        catalog = _FakeCatalog(
            execute_results={
                "write_file": {
                    "awaiting_approval": True,
                    "pending_tool_call": {"server": "builtin", "name": "write_file", "args": {}},
                },
            },
        )
        llm = _ScriptedLoopLLM(
            [
                _action("SELECT_TOOLS", selected_tool_names=["write_file"]),
                _action(
                    "TOOL_CALLS",
                    tool_calls=[
                        {"id": "c1", "name": "write_file", "arguments": {"path": "/tmp/a"}}
                    ],
                ),
                _action("FINAL_ANSWER", final_answer="写入完成"),
            ]
        )
        loop = DynamicToolLoop(llm, catalog)
        out = await loop.run(_loop_state())
        out = await loop.run(_loop_state(**out))
        assert out["awaiting_approval"] is True
        assert out["tool_loop_active"] is True

        # 审批通过 → 恢复：重放该调用，再进入下一轮
        st = _loop_state()
        st.update(out)
        st["approval_decision"] = "approve"
        st["awaiting_approval"] = False
        resumed = await loop.run(st)
        # 第一次执行（暂停前）+ 审批通过后重放（fake pending 的 args 为空）
        assert catalog.executed[0] == ("write_file", {"path": "/tmp/a"})
        assert len(catalog.executed) == 2
        # approve 重放也命中 awaiting（fake 永远返回 awaiting）→ 保守保持暂停
        assert resumed.get("awaiting_approval") is True

    async def test_hitl_pause_reject_resume(self):
        catalog = _FakeCatalog(
            execute_results={
                "write_file": {
                    "awaiting_approval": True,
                    "pending_tool_call": {"server": "builtin", "name": "write_file", "args": {}},
                }
            },
        )
        llm = _ScriptedLoopLLM(
            [
                _action("SELECT_TOOLS", selected_tool_names=["write_file"]),
                _action(
                    "TOOL_CALLS",
                    tool_calls=[
                        {"id": "c1", "name": "write_file", "arguments": {"path": "/tmp/a"}}
                    ],
                ),
                _action("FINAL_ANSWER", final_answer="已取消"),
            ]
        )
        loop = DynamicToolLoop(llm, catalog)
        out = await loop.run(_loop_state())
        out = await loop.run(_loop_state(**out))
        assert out["awaiting_approval"] is True
        st = _loop_state()
        st.update(out)
        st["approval_decision"] = "reject"
        st["awaiting_approval"] = False
        resumed = await loop.run(st)
        assert resumed["tool_results"][0]["error"] == "user_rejected"
        assert resumed["final_answer"] == "已取消"

    async def test_max_turns_cap(self):
        catalog = _FakeCatalog()
        llm = _ScriptedLoopLLM([_action("SELECT_TOOLS", selected_tool_names=["get_weather"])] * 20)
        loop = DynamicToolLoop(llm, catalog, max_turns=3)
        out = await loop.run(_loop_state())
        out = await loop.run(_loop_state(**out))
        out = await loop.run(_loop_state(**out))
        assert "预算已用尽" not in out.get("final_answer", "")
        out = await loop.run(_loop_state(**out))
        # 2026-08-25 文案诚实化：不再说「请缩小问题范围」，明确是预算耗尽可接续
        assert "预算已用尽" in out["final_answer"]
        assert "继续" in out["final_answer"]

    async def test_stagnant_fuse_trips_after_consecutive_failed_turns(self):
        """停滞熔断（2026-08-25）：连续 3 轮零成功执行 → 提前终止；
        防小模型重复同一失败调用无限空转（预算不再是唯一拦截）。
        """
        catalog = _FakeCatalog(
            execute_results={"get_weather": {"name": "get_weather", "ok": False, "error": "boom"}}
        )
        llm = _ScriptedLoopLLM(
            [_action("SELECT_TOOLS", selected_tool_names=["get_weather"])]
            + [
                _action(
                    "TOOL_CALLS",
                    tool_calls=[{"id": f"c{i}", "name": "get_weather", "arguments": {}}],
                )
                for i in range(3)
            ]
        )
        loop = DynamicToolLoop(llm, catalog, max_turns=20)
        out = await loop.run(_loop_state())  # SELECT_TOOLS
        out = await loop.run(_loop_state(**out))  # 失败轮 1（streak=1）
        assert out.get("final_answer") is None
        out = await loop.run(_loop_state(**out))  # 失败轮 2（streak=2）
        assert out.get("final_answer") is None
        out = await loop.run(_loop_state(**out))  # 失败轮 3（streak=3 → 熔断）
        assert "均无有效结果" in out["final_answer"]
        assert out["tool_loop_active"] is False

    async def test_stagnant_streak_resets_on_success(self):
        """有进展就继续：成功执行一轮即清零，不误杀长链任务。"""
        catalog = _FakeCatalog()
        llm = _ScriptedLoopLLM(
            [_action("SELECT_TOOLS", selected_tool_names=["get_weather"])]
            + [
                _action(
                    "TOOL_CALLS",
                    tool_calls=[{"id": f"c{i}", "name": "get_weather", "arguments": {}}],
                )
                for i in range(6)
            ]
            + [_action("FINAL_ANSWER", final_answer="done")]
        )
        loop = DynamicToolLoop(llm, catalog, max_turns=20)
        out = await loop.run(_loop_state())

        # 真实图状态合并会保留 registered_tools；_loop_state 每轮新建 state，
        # 需手动携带（TOOL_CALLS 增量不回传该字段）
        def _next(o: dict) -> dict:
            return _loop_state(**{**o, "registered_tools": o.get("registered_tools") or reg})

        reg = out.get("registered_tools") or []
        for _ in range(6):  # 6 轮成功执行（远超旧默认 8 轮预算的场景）
            out = await loop.run(_next(out))
            assert out.get("tool_stagnant_streak", 0) == 0
            assert out.get("final_answer") is None
        out = await loop.run(_next(out))
        assert out["final_answer"] == "done"

    async def test_llm_error_fallback(self):
        catalog = _FakeCatalog()
        llm = _ScriptedLoopLLM([RuntimeError("llm down")])
        loop = DynamicToolLoop(llm, catalog)
        out = await loop.run(_loop_state())
        assert "工具编排失败" in out["final_answer"]

    async def test_fallback_flag_conservative(self):
        catalog = _FakeCatalog()
        llm = _ScriptedLoopLLM([{"action": "FINAL_ANSWER", "_fallback": True}])
        loop = DynamicToolLoop(llm, catalog)
        out = await loop.run(_loop_state())
        assert "无法完成" in out["final_answer"]


# ---- 路由 ------------------------------------------------------------------


class TestToolLoopRouting:
    @pytest.mark.parametrize(
        ("patch", "expected"),
        [
            ({"awaiting_approval": True}, "hitl_gate"),
            ({"final_answer": "done"}, "responder"),
            ({"tool_loop_active": False}, "responder"),
            ({"tool_loop_active": True, "load_stage": "CANDIDATE_REGISTERED"}, "tool_orchestrator"),
            (
                {"tool_loop_active": True, "tool_results": [{"name": "x", "ok": True}]},
                "tool_orchestrator",
            ),
            ({}, "responder"),
        ],
    )
    def test_route(self, patch: dict, expected: str):
        st = empty_state("x")
        st.update(patch)
        assert route_after_tool_loop(st) == expected


# ---- LMRouter 动作解析 ------------------------------------------------------


class TestActionParsing:
    def _parse(self, raw: dict, **kw: Any) -> dict | None:
        return _parse_orchestration_action(json.dumps(raw), **kw)

    def test_select_tools_valid(self):
        out = self._parse(
            _action("SELECT_TOOLS", selected_tool_names=["a"]),
            summary_names={"a", "b"},
            registered_names=set(),
            full_loaded=False,
            max_selected=5,
        )
        assert out is not None and out["action"] == "SELECT_TOOLS"

    def test_select_tools_rejects_unknown_name(self):
        out = self._parse(
            _action("SELECT_TOOLS", selected_tool_names=["ghost"]),
            summary_names={"a"},
            registered_names=set(),
            full_loaded=False,
            max_selected=5,
        )
        assert out is None

    def test_tool_calls_requires_registered(self):
        out = self._parse(
            _action("TOOL_CALLS", tool_calls=[{"id": "c1", "name": "ghost", "arguments": {}}]),
            summary_names=set(),
            registered_names={"a"},
            full_loaded=False,
            max_selected=5,
        )
        assert out is None

    def test_tool_calls_rejects_duplicate_ids(self):
        out = self._parse(
            _action(
                "TOOL_CALLS",
                tool_calls=[
                    {"id": "c1", "name": "a", "arguments": {}},
                    {"id": "c1", "name": "a", "arguments": {}},
                ],
            ),
            summary_names=set(),
            registered_names={"a"},
            full_loaded=False,
            max_selected=5,
        )
        assert out is None

    def test_request_full_tools_after_full_loaded_rejected(self):
        out = self._parse(
            _action("REQUEST_FULL_TOOLS"),
            summary_names=set(),
            registered_names=set(),
            full_loaded=True,
            max_selected=5,
        )
        assert out is None

    def test_ask_user_requires_message(self):
        out = self._parse(
            _action("ASK_USER", ask_user_message=""),
            summary_names=set(),
            registered_names=set(),
            full_loaded=False,
            max_selected=5,
        )
        assert out is None

    def test_unknown_action_rejected(self):
        out = self._parse(
            _action("BOGUS"),
            summary_names=set(),
            registered_names=set(),
            full_loaded=False,
            max_selected=5,
        )
        assert out is None

    async def test_orchestrate_tools_fills_prompt(self, monkeypatch):
        router = LMRouter()

        async def fake_route(*, task: str, prompt: str) -> str:
            assert task == "tool_orchestrate"
            for placeholder in (
                "{{LOAD_STAGE}}",
                "{{USER_INPUT}}",
                "{{TOOL_SUMMARIES}}",
                "{{REGISTERED_TOOLS}}",
                "{{FULL_TOOLSET_LOADED}}",
                "{{TOOL_RESULTS}}",
                "{{MAX_SELECTED_TOOLS}}",
            ):
                assert placeholder not in prompt
            return json.dumps(_action("FINAL_ANSWER", final_answer="ok"))

        monkeypatch.setattr(router, "route", fake_route)
        action = await router.orchestrate_tools(
            load_stage="SUMMARY_ONLY",
            user_input="查天气",
            messages=[],
            tool_summaries=[{"name": "a"}],
            registered_tools=[],
            full_toolset_loaded=False,
            tool_results=[],
        )
        assert action["action"] == "FINAL_ANSWER"
        assert action["final_answer"] == "ok"

    async def test_orchestrate_tools_unparseable_falls_back(self, monkeypatch):
        router = LMRouter()

        async def fake_route(*, task: str, prompt: str) -> str:
            return "not json"

        monkeypatch.setattr(router, "route", fake_route)
        action = await router.orchestrate_tools(
            load_stage="SUMMARY_ONLY",
            user_input="x",
            messages=[],
            tool_summaries=[],
            registered_tools=[],
            full_toolset_loaded=False,
            tool_results=[],
        )
        assert action["_fallback"] is True


# ---- 新增常用工具 ------------------------------------------------------------


class TestExtraTools:
    def test_datetime_now(self):
        result = builtin_datetime_now()
        assert result.ok is True
        assert isinstance(result.content, dict)
        assert "T" in result.content["datetime"]  # ISO 8601
        assert result.content["weekday"].startswith("星期")
        assert result.meta.get("utc_offset")  # 本地时区偏移非空

    def test_datetime_now_lunar(self):
        """zhdate 可用时返回农历中文描述。"""
        pytest.importorskip("zhdate")
        result = builtin_datetime_now()
        assert result.ok is True
        assert result.meta["lunar_available"] is True
        assert result.content["lunar"]  # 如「二零二六年正月初一」

    def test_datetime_now_explicit_offset(self):
        result = builtin_datetime_now(tz_offset_hours=8, include_lunar=False)
        assert result.ok is True
        assert result.content["datetime"].endswith("+08:00")
        assert "lunar" not in result.content

    # ---- date_parse：相对时间 → 绝对日期（基准日 2026-08-06 周四）----

    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("今天", "2026-08-06"),
            ("明天", "2026-08-07"),
            ("后天", "2026-08-08"),
            ("大后天", "2026-08-09"),
            ("昨天", "2026-08-05"),
            ("前天", "2026-08-04"),
            ("3 天前".replace(" ", ""), "2026-08-03"),
            ("五天后", "2026-08-11"),
            ("下周一", "2026-08-10"),
            ("本周五", "2026-08-07"),
            ("周三", "2026-08-12"),  # 本周已过周三 → 指下周三
            ("本月底", "2026-08-31"),
            ("下月底", "2026-09-30"),
            ("2026-08-07", "2026-08-07"),  # 显式日期透传
        ],
    )
    def test_date_parse_single_date(self, expr, expected):
        from agent.builtin.extra import builtin_date_parse

        result = builtin_date_parse(expression=expr, base_date="2026-08-06")
        assert result.ok is True, f"{expr}: {result.error}"
        assert result.content["type"] == "date"
        assert result.content["date"] == expected

    def test_date_parse_range_recent_days(self):
        from agent.builtin.extra import builtin_date_parse

        result = builtin_date_parse(expression="最近三天", base_date="2026-08-06")
        assert result.ok is True
        assert result.content["type"] == "range"
        assert result.content["start"] == "2026-08-04"
        assert result.content["end"] == "2026-08-06"
        assert result.content["days"] == 3

    def test_date_parse_weekend(self):
        from agent.builtin.extra import builtin_date_parse

        result = builtin_date_parse(expression="这个周末", base_date="2026-08-06")
        assert result.ok is True
        assert result.content["type"] == "range"
        assert result.content["start"] == "2026-08-08"  # 周六
        assert result.content["end"] == "2026-08-09"  # 周日

    def test_date_parse_unparsable_asks_user(self):
        from agent.builtin.extra import builtin_date_parse

        result = builtin_date_parse(expression="去年的那天", base_date="2026-08-06")
        assert result.ok is False
        assert result.error == "unparsable_expression"
        assert "追问" in (result.hint or "")  # 提示上游追问而非猜测

    def test_date_parse_registered(self):
        """date_parse 已注册进目录（可被工具循环选中）。"""
        from agent.builtin.models import BUILTIN_TOOL_NAMES
        from agent.builtin.registry import TOOL_RISK_LEVEL

        assert "date_parse" in BUILTIN_TOOL_NAMES
        assert TOOL_RISK_LEVEL["date_parse"] == "low"

    def test_uuid4(self):
        result = builtin_uuid4()
        assert result.ok is True
        assert len(str(result.content)) == 36

    def test_csv_parse_with_header(self):
        result = builtin_csv_parse(text="name,age\nalice,30\nbob,25", has_header=True)
        assert result.ok is True
        assert result.content["header"] == ["name", "age"]
        assert result.content["row_count"] == 2

    def test_text_split(self):
        result = builtin_text_split(text="a" * 100, max_chars=30)
        assert result.ok is True
        assert len(result.content) == 4

    async def test_http_get_invalid_url(self):
        result = await builtin_http_get(url="ftp://example.com/x")
        assert result.ok is False
        assert result.error == "invalid_url"

    async def test_http_get_success(self, monkeypatch):
        import httpx

        class _FakeResponse:
            status_code = 200
            content = b'{"ok": true}'
            headers = {"content-type": "application/json"}  # noqa: RUF012 测试 fake 常量

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, url, headers=None):
                return _FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        result = await builtin_http_get(url="https://example.com/api")
        assert result.ok is True
        assert result.content["status_code"] == 200


# ---- Rust 工具 Python 兜底 ---------------------------------------------------


class TestPythonFallbacks:
    def test_stat_file(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("hello", encoding="utf-8")
        result = builtin_stat_file_py(path=str(p))
        assert result.ok is True
        assert result.content["size"] == 5

    def test_find_and_glob(self, tmp_path):
        (tmp_path / "one.py").write_text("", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "two.py").write_text("", encoding="utf-8")
        found = builtin_find_py(path=str(tmp_path), pattern="*.py")
        assert found.ok is True
        assert len(found.content) >= 2
        globbed = builtin_glob_py(pattern="**/*.py", base_dir=str(tmp_path))
        assert globbed.ok is True
        assert len(globbed.content) >= 2

    def test_hash_and_base64(self, tmp_path):
        p = tmp_path / "b.bin"
        p.write_bytes(b"abc")
        h = builtin_hash_py(path=str(p), algorithm="sha256")
        assert h.ok is True
        assert len(str(h.content)) == 64
        enc = builtin_base64_py(mode="encode", data="hello")
        assert enc.ok is True
        dec = builtin_base64_py(mode="decode", data=str(enc.content))
        assert dec.ok is True
        assert dec.content == "hello"


# ---- 图级 e2e ---------------------------------------------------------------


class TestGraphEndToEnd:
    async def test_tool_loop_path_runs_to_answer(self, monkeypatch):
        monkeypatch.setattr(settings, "tool_loop_enabled", True)

        class _LLM:
            def __init__(self):
                self.actions = [
                    _action("SELECT_TOOLS", selected_tool_names=["calculator"]),
                    _action(
                        "TOOL_CALLS",
                        tool_calls=[
                            {
                                "id": "c1",
                                "name": "calculator",
                                "arguments": {"expression": "2 * 21"},
                                "purpose": "计算",
                            }
                        ],
                    ),
                    _action("FINAL_ANSWER", final_answer="结果是 42"),
                ]
                self.summarise_calls = 0

            async def classify_intent(self, text: str) -> str:
                return "query"

            async def plan(self, *, intent, user_prompt, history, tool_specs):
                return [], "no plan"

            async def decompose(self, **kwargs):
                return {
                    "decision": {
                        "mode": "TOOL_ONLY",
                        "should_enable_subagent": False,
                        "execution_allowed": True,
                        "user_confirmation_required": False,
                        "confidence": 0.9,
                        "reason": "tool task",
                        "clarifying_questions": [],
                        "confirmation_message": None,
                        "refusal_message": None,
                    },
                    "selected_subagents": [],
                }

            async def orchestrate_tools(self, **kwargs):
                return self.actions.pop(0)

            async def summarise(self, *, intent, user_prompt, plan, results, history=None):
                self.summarise_calls += 1
                return "工具结果汇总答案", []

        graph = compile_graph(Runtime(llm=_LLM(), mcp=None))
        st = empty_state("帮我算一下 2 乘以 21")
        st["run_id"] = "run-loop"
        result = await graph.ainvoke(st)
        assert result["final_answer"] == "结果是 42"
        assert result["load_stage"] == "CANDIDATE_REGISTERED"
        assert result["tool_results"][0]["result"]["value"] == 42
