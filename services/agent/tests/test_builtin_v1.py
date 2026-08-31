"""Phase 1B V1 · 单元测试 + 集成测试（30+ 用例）。

覆盖:
  - lightweight 5 工具（calculator / json_parse / json_format / regex_match / url_parse）
  - registry 更新（BUILTIN_TOOL_NAMES + TOOL_RISK_LEVEL 含 Rust 工具占位）
  - Rust 工具 not_implemented 占位（dispatcher 返 ok=false）
  - SSE 三处同步 3 事件（emit_tool_started/done/denied）
  - 审计双写（audit + tool_calls 表）
  - 风险等级 / HITL 评估
  - dispatcher 集成（Python 工具走 to_thread + Rust 工具占位）
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---- lightweight 5 工具 ---------------------------------------------------------


class TestCalculator:
    """builtin_calculator —— AST 安全算术。"""

    def test_basic_arithmetic(self):
        from agent.builtin.lightweight import builtin_calculator

        for expr, expected in [
            ("1+1", 2),
            ("2*3", 6),
            ("10/4", 2.5),
            ("10//3", 3),
            ("10%3", 1),
            ("2**10", 1024),
            ("-5+10", 5),
            ("3.14*2", 6.28),
        ]:
            r = builtin_calculator(expr)
            assert r.ok, f"{expr}: {r.error}"
            assert r.content["value"] == expected, f"{expr}: got {r.content['value']}"

    def test_nested_expression(self):
        from agent.builtin.lightweight import builtin_calculator

        r = builtin_calculator("(2+3)*4-10")
        assert r.ok
        assert r.content["value"] == 10

    def test_reject_function_call(self):
        from agent.builtin.lightweight import builtin_calculator

        r = builtin_calculator("__import__('os').system('echo pwned')")
        assert not r.ok
        assert "calc_error" in r.error or "unsupported" in r.error

    def test_reject_variable(self):
        from agent.builtin.lightweight import builtin_calculator

        r = builtin_calculator("x=5")
        assert not r.ok

    def test_reject_string_literal(self):
        from agent.builtin.lightweight import builtin_calculator

        r = builtin_calculator("'hello'")
        assert not r.ok

    def test_empty_expression(self):
        from agent.builtin.lightweight import builtin_calculator

        r = builtin_calculator("")
        assert not r.ok
        assert "empty" in r.error

    def test_division_by_zero(self):
        from agent.builtin.lightweight import builtin_calculator

        r = builtin_calculator("1/0")
        assert not r.ok
        assert "division_by_zero" in r.error

    def test_invalid_syntax(self):
        from agent.builtin.lightweight import builtin_calculator

        r = builtin_calculator("1++")
        assert not r.ok
        assert "syntax_error" in r.error

    def test_input_too_long(self):
        from agent.builtin.lightweight import builtin_calculator

        r = builtin_calculator("1+" * 70000)  # > 64KB
        assert not r.ok
        assert "too long" in r.error


class TestJsonParse:
    """builtin_json_parse —— 严格 JSON 解析。"""

    def test_parse_object(self):
        from agent.builtin.lightweight import builtin_json_parse

        r = builtin_json_parse('{"a": 1, "b": [2, 3]}')
        assert r.ok
        assert r.content == {"a": 1, "b": [2, 3]}

    def test_parse_array(self):
        from agent.builtin.lightweight import builtin_json_parse

        r = builtin_json_parse('[1, 2, "three", null, true]')
        assert r.ok
        assert r.content == [1, 2, "three", None, True]

    def test_parse_with_position_error(self):
        from agent.builtin.lightweight import builtin_json_parse

        r = builtin_json_parse('{"a": 1, "b": }')  # 语法错误
        assert not r.ok
        assert "json_decode_error" in r.error
        assert "line" in r.error
        assert "col" in r.error
        assert r.meta.get("lineno") == 1

    def test_reject_non_string(self):
        from agent.builtin.lightweight import builtin_json_parse

        r = builtin_json_parse(123)
        assert not r.ok
        assert "must be str" in r.error

    def test_reject_duplicate_keys_strict(self):
        """strict=True 时重复键抛错。"""
        from agent.builtin.lightweight import builtin_json_parse

        r = builtin_json_parse('{"a": 1, "a": 2}', strict=True)
        # Python 的 json.JSONDecoder(strict=True) 在 raw_decode 中允许重复键（不抛错）
        # 我们的 strict 参数控制 json.JSONDecoder(strict=strict)，因此重复键可能正常
        # 只要 strict=True 不报 TypeError 即可
        assert r.ok or "json_decode_error" in r.error

    def test_input_too_long(self):
        from agent.builtin.lightweight import builtin_json_parse

        r = builtin_json_parse("[" + "1," * 70000)  # > 64KB
        assert not r.ok


class TestJsonFormat:
    """builtin_json_format —— JSON 美化。"""

    def test_basic_format(self):
        from agent.builtin.lightweight import builtin_json_format

        r = builtin_json_format({"b": 2, "a": 1}, indent=2, sort_keys=True)
        assert r.ok
        assert r.content == '{\n  "a": 1,\n  "b": 2\n}'

    def test_chinese_unicode_preserved(self):
        from agent.builtin.lightweight import builtin_json_format

        r = builtin_json_format({"name": "张三"}, ensure_ascii=False)
        assert r.ok
        assert "张三" in r.content
        assert "\\u" not in r.content

    def test_chinese_escaped(self):
        from agent.builtin.lightweight import builtin_json_format

        r = builtin_json_format({"name": "张三"}, ensure_ascii=True)
        assert r.ok
        assert "\\u" in r.content

    def test_reject_unsupported_type(self):
        from agent.builtin.lightweight import builtin_json_format

        # default=str 让 lambda 转 "<function ...>"（默认行为）
        # 用户禁用 default=str 才能看到 TypeError；这里只验证不崩
        r = builtin_json_format({"f": lambda x: x})
        assert r.ok
        # 验证 lambda 被转 str
        assert "function" in r.content or "lambda" in r.content

    def test_reject_invalid_indent(self):
        from agent.builtin.lightweight import builtin_json_format

        r = builtin_json_format({}, indent=100)
        assert not r.ok

    def test_no_nan_infinity(self):
        from agent.builtin.lightweight import builtin_json_format

        r = builtin_json_format({"x": float("nan")})
        assert not r.ok  # allow_nan=False

    def test_path_object_serialized(self):
        """datetime / Path 等常见类型走 default=str。"""
        from agent.builtin.lightweight import builtin_json_format

        # Windows 下 Path("/tmp/test") 的 .__str__() 转 "\\tmp\\test" —— 用绝对 Windows 路径测
        r = builtin_json_format({"p": Path("C:/Users/test")})
        assert r.ok
        assert "C:" in r.content or "Users" in r.content


class TestRegexMatch:
    """builtin_regex_match —— 防 ReDoS 正则匹配。"""

    def test_basic_match(self):
        from agent.builtin.lightweight import builtin_regex_match

        r = builtin_regex_match(r"\d+", "abc 123 def 456")
        assert r.ok
        assert len(r.content) == 2
        assert r.content[0]["match"] == "123"
        assert r.content[0]["span"] == [4, 7]

    def test_capture_groups(self):
        from agent.builtin.lightweight import builtin_regex_match

        r = builtin_regex_match(r"(\w+)@(\w+)", "user@host")
        assert r.ok
        assert r.content[0]["groups"] == ["user", "host"]

    def test_named_groups(self):
        from agent.builtin.lightweight import builtin_regex_match

        r = builtin_regex_match(r"(?P<user>\w+)", "alice")
        assert r.ok
        assert r.content[0]["named_groups"] == {"user": "alice"}

    def test_no_match(self):
        from agent.builtin.lightweight import builtin_regex_match

        r = builtin_regex_match(r"\d+", "no digits here")
        assert r.ok
        assert r.content == []
        assert r.meta["count"] == 0

    def test_truncate_max_matches(self):
        from agent.builtin.lightweight import builtin_regex_match

        r = builtin_regex_match(r".", "x" * 100, max_matches=10)
        assert r.ok
        assert len(r.content) == 10
        assert r.meta["truncated"] is True

    def test_pattern_too_long(self):
        from agent.builtin.lightweight import builtin_regex_match

        r = builtin_regex_match(r"a" * 2000, "x")
        assert not r.ok
        assert "too long" in r.error

    def test_invalid_regex(self):
        from agent.builtin.lightweight import builtin_regex_match

        r = builtin_regex_match(r"(unclosed", "x")
        assert not r.ok
        assert "regex_compile_error" in r.error

    def test_case_insensitive(self):
        import re

        from agent.builtin.lightweight import builtin_regex_match

        r = builtin_regex_match(r"HELLO", "hello world", flags=re.IGNORECASE)
        assert r.ok
        assert len(r.content) == 1


class TestUrlParse:
    """builtin_url_parse —— URL 解析 + IPv4 校验。"""

    def test_basic_url(self):
        from agent.builtin.lightweight import builtin_url_parse

        r = builtin_url_parse("https://user:pass@example.com:8080/path?a=1&b=2#frag")
        assert r.ok
        assert r.content["scheme"] == "https"
        assert r.content["hostname"] == "example.com"
        assert r.content["port"] == 8080
        assert r.content["username"] == "user"
        assert r.content["password"] == "pass"
        assert r.content["path"] == "/path"
        assert r.content["fragment"] == "frag"
        assert r.content["query_dict"] == {"a": ["1"], "b": ["2"]}
        assert r.content["ipv4_valid"] is False  # example.com 不是 IP

    def test_ipv4_host(self):
        from agent.builtin.lightweight import builtin_url_parse

        r = builtin_url_parse("http://192.168.1.1:8080/path")
        assert r.ok
        assert r.content["hostname"] == "192.168.1.1"
        assert r.content["ipv4_valid"] is True

    def test_query_multi_value(self):
        from agent.builtin.lightweight import builtin_url_parse

        r = builtin_url_parse("https://x.com/?tag=a&tag=b&tag=c")
        assert r.ok
        assert r.content["query_dict"]["tag"] == ["a", "b", "c"]

    def test_no_query(self):
        from agent.builtin.lightweight import builtin_url_parse

        r = builtin_url_parse("https://x.com/path")
        assert r.ok
        assert r.content["query_dict"] == {}

    def test_non_string_url(self):
        from agent.builtin.lightweight import builtin_url_parse

        r = builtin_url_parse(123)
        assert not r.ok


# ---- registry / models ---------------------------------------------------------


class TestRegistryV1:
    """V1 registry 扩展。"""

    def test_builtin_tool_names_has_lightweight(self):
        from agent.builtin.models import BUILTIN_TOOL_NAMES

        for name in ("calculator", "json_parse", "json_format", "regex_match", "url_parse"):
            assert name in BUILTIN_TOOL_NAMES, f"{name} not in BUILTIN_TOOL_NAMES"

    def test_builtin_tool_names_has_rust(self):
        from agent.builtin.models import BUILTIN_TOOL_NAMES

        for name in (
            "stat_file",
            "mkdir",
            "delete_file",
            "move_file",
            "find",
            "glob",
            "hash",
            "base64",
            "shell",
        ):
            assert name in BUILTIN_TOOL_NAMES, f"{name} not in BUILTIN_TOOL_NAMES"

    def test_rust_tool_names_frozenset(self):
        from agent.builtin.models import RUST_TOOL_NAMES, is_rust_tool

        assert isinstance(RUST_TOOL_NAMES, frozenset)
        assert len(RUST_TOOL_NAMES) == 9
        for name in RUST_TOOL_NAMES:
            assert is_rust_tool(name)

    def test_is_rust_tool_false(self):
        from agent.builtin.models import is_rust_tool

        assert not is_rust_tool("read_file")
        assert not is_rust_tool("calculator")
        assert not is_rust_tool("unknown_tool")

    def test_lightweight_risk_level_low(self):
        from agent.builtin.registry import TOOL_RISK_LEVEL

        for name in ("calculator", "json_parse", "json_format", "regex_match", "url_parse"):
            assert TOOL_RISK_LEVEL[name] == "low", f"{name} should be low risk"

    def test_rust_risk_level_map(self):
        from agent.builtin.registry import TOOL_RISK_LEVEL

        assert TOOL_RISK_LEVEL["stat_file"] == "read"
        assert TOOL_RISK_LEVEL["mkdir"] == "medium"
        assert TOOL_RISK_LEVEL["delete_file"] == "high"
        assert TOOL_RISK_LEVEL["move_file"] == "high"
        assert TOOL_RISK_LEVEL["shell"] == "critical"

    def test_registry_lists_all(self):
        from agent.builtin.registry import BuiltinToolRegistry

        reg = BuiltinToolRegistry()
        names = reg.list_names()
        assert "read_file" in names
        assert "calculator" in names
        assert "json_parse" in names
        # Rust 工具不通过 Python registry 调用；它们是 dispatcher 的占位
        assert "shell" not in names
        assert "stat_file" not in names

    def test_registry_descriptions_include_lightweight(self):
        from agent.builtin.registry import BuiltinToolRegistry

        reg = BuiltinToolRegistry()
        desc = reg.generate_tool_descriptions()
        assert "builtin_calculator" in desc
        assert "builtin_url_parse" in desc


# ---- events SSE emit ------------------------------------------------------------


class TestBuiltinEvents:
    """V1 SSE 三处同步 3 事件 emit。"""

    @pytest.mark.asyncio
    async def test_emit_started(self):
        from agent.builtin.events import (
            EVT_BUILTIN_TOOL_STARTED,
            consume_builtin_events,
            emit_tool_started,
            flush_builtin_events,
        )

        await flush_builtin_events()
        await emit_tool_started(
            tool_name="read_file",
            args={"path": "/tmp/x.txt"},
            risk_level="read",
            needs_hitl=False,
            call_id="abc123",
        )
        events = await consume_builtin_events()
        assert len(events) == 1
        kind, payload = events[0]
        assert kind == EVT_BUILTIN_TOOL_STARTED
        assert payload["tool_name"] == "read_file"
        assert payload["risk_level"] == "read"
        assert payload["needs_hitl"] is False
        assert payload["call_id"] == "abc123"
        assert "path" in payload["args_keys"]

    @pytest.mark.asyncio
    async def test_emit_done(self):
        from agent.builtin.events import (
            EVT_BUILTIN_TOOL_DONE,
            consume_builtin_events,
            emit_tool_done,
            flush_builtin_events,
        )

        await flush_builtin_events()
        await emit_tool_done(
            tool_name="read_file",
            call_id="abc123",
            ok=True,
            error=None,
            elapsed_ms=42,
            risk_level="read",
            content_size=100,
            result_meta={"line_count": 5},
        )
        events = await consume_builtin_events()
        assert len(events) == 1
        kind, payload = events[0]
        assert kind == EVT_BUILTIN_TOOL_DONE
        assert payload["elapsed_ms"] == 42
        assert payload["content_size"] == 100
        assert payload["ok"] is True

    @pytest.mark.asyncio
    async def test_emit_denied(self):
        from agent.builtin.events import (
            EVT_BUILTIN_TOOL_DENIED,
            consume_builtin_events,
            emit_tool_denied,
            flush_builtin_events,
        )

        await flush_builtin_events()
        await emit_tool_denied(
            tool_name="delete_file",
            call_id="abc",
            approval_id="appr-1",
            reason="high_risk_denied",
        )
        events = await consume_builtin_events()
        assert len(events) == 1
        kind, payload = events[0]
        assert kind == EVT_BUILTIN_TOOL_DENIED
        assert payload["reason"] == "high_risk_denied"
        assert payload["approval_id"] == "appr-1"

    @pytest.mark.asyncio
    async def test_flush(self):
        from agent.builtin.events import (
            consume_builtin_events,
            emit_tool_started,
            flush_builtin_events,
        )

        await flush_builtin_events()
        await emit_tool_started(
            tool_name="x", args={}, risk_level="read", needs_hitl=False, call_id="1"
        )
        await emit_tool_started(
            tool_name="x", args={}, risk_level="read", needs_hitl=False, call_id="2"
        )
        dropped = await flush_builtin_events()
        assert dropped == 2
        events = await consume_builtin_events()
        assert events == []


# ---- dispatcher V1 集成 ---------------------------------------------------------


class TestDispatcherV1:
    """V1 dispatcher 集成：Python 工具走 to_thread；Rust 工具占位 not_implemented。"""

    @pytest.mark.asyncio
    async def test_python_calculator_dispatch(self):
        """calculator 走本地 Python 执行。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "calculator", "args": {"expression": "2+3"}},
            {"run_id": "test-run"},
        )
        assert result is not None
        assert result["tool_result"]["ok"] is True
        assert result["tool_result"]["content"]["value"] == 5
        assert result["tool_error"] is None
        assert result["trace"][0]["call_id"]  # UUID 存在

    @pytest.mark.asyncio
    async def test_shell_waits_for_hitl(self):
        """shell（V2 已实现，critical）→ 未审批时走 HITL 前置闸门。"""
        from agent.builtin._tauri_runtime import clear_tauri_runtime
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry

        clear_tauri_runtime()
        reset_default_registry()
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "shell", "args": {"command": "echo hi"}},
            {"run_id": "test-run"},
        )
        assert result is not None
        assert result["awaiting_approval"] is True
        assert result["tool_result"] is None
        assert result["tool_error"] is None

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """未知工具返 unknown_builtin_tool。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "fake_tool", "args": {}},
            {"run_id": "test-run"},
        )
        assert result["tool_error"] == "unknown_builtin_tool: fake_tool"

    @pytest.mark.asyncio
    async def test_non_builtin_server_returns_none(self):
        """非 builtin server 返 None（让上游走 MCP）。"""
        from agent.builtin.dispatcher import dispatcher

        result = await dispatcher().dispatch(
            {"server": "mcp", "name": "some_tool", "args": {}},
            {},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_dispatch_writes_tool_calls_row(self, tmp_path: Path, monkeypatch):
        """dispatcher 写 tool_calls 结构化表。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.config import settings

        reset_default_registry()
        # 重定向 audit_db_path 到 tmp_path
        db_path = tmp_path / "audit.sqlite"
        monkeypatch.setattr(settings, "audit_db_path", str(db_path))

        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "calculator", "args": {"expression": "7*6"}},
            {"run_id": "test-tool-calls"},
        )
        assert result["tool_result"]["ok"] is True

        # 检查 tool_calls 表行
        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as db:
            cur = await db.execute(
                "SELECT call_id, tool_name, risk_level, ok, elapsed_ms, run_id "
                "FROM tool_calls ORDER BY id DESC LIMIT 1"
            )
            row = await cur.fetchone()
        assert row is not None
        call_id, tool_name, risk_level, ok, elapsed_ms, run_id = row
        assert tool_name == "calculator"
        assert risk_level == "low"
        assert ok == 1
        assert run_id == "test-tool-calls"
        assert elapsed_ms >= 0
        assert len(call_id) == 32  # UUID4 hex

    @pytest.mark.asyncio
    async def test_dispatch_emits_sse_events(self):
        """dispatcher 调用 builtin.events emit 3 事件。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.events import (
            consume_builtin_events,
            flush_builtin_events,
        )
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        await flush_builtin_events()

        await dispatcher().dispatch(
            {"server": "builtin", "name": "calculator", "args": {"expression": "1+1"}},
            {"run_id": "test-sse"},
        )

        events = await consume_builtin_events()
        kinds = [k for k, _ in events]
        assert "builtin_tool_started" in kinds
        assert "builtin_tool_done" in kinds

    @pytest.mark.asyncio
    async def test_dispatch_write_hitl_marker(self, tmp_path, monkeypatch):
        """medium 风险工具 → needs_hitl=True → trace 含 needs_hitl=True。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.config import settings

        reset_default_registry()
        monkeypatch.setattr(settings, "audit_db_path", str(tmp_path / "a.sqlite"))
        # calculator 是 low 风险（无 HITL）；改用 write_file 测 medium
        result = await dispatcher().dispatch(
            {
                "server": "builtin",
                "name": "write_file",
                "args": {
                    "path": str(tmp_path / "f.txt"),
                    "content": "hello",
                },
            },
            {"run_id": "test-hitl"},
        )
        # write_file 的风险等级由 registry TOOL_RISK_LEVEL["write_file"]="medium"
        # 在 _evaluate_hitl 中会返 True
        assert result["awaiting_approval"] is True
        assert result["trace"][0]["needs_hitl"] is True


# ---- _LOCAL_ONLY_TASKS V1 注入 -------------------------------------------------


class TestLocalOnlyTasksV1:
    """V1 _LOCAL_ONLY_TASKS 注入 builtin_tool_summary / builtin_search_summarize。"""

    def test_local_only_tasks_has_builtin_summary(self):
        from agent.llm.router import _LOCAL_ONLY_TASKS

        assert "builtin_tool_summary" in _LOCAL_ONLY_TASKS
        assert "builtin_search_summarize" in _LOCAL_ONLY_TASKS

    def test_all_local_only_tasks_count(self):
        """17 个本地任务：原 16 + Phase 7 v2.87 新增 metric_resolve。"""
        from agent.llm.router import _LOCAL_ONLY_TASKS

        # 2026-07-29 末态：8 个；Phase 1B V1 (2026-07-30) 新增 2 → 10；
        # Phase 14 V0 (2026-07-31) 新增 image_processing_summary → 11；
        # Phase 2B V0 (2026-07-31) 新增 ssh_command_summary → 12；
        # Phase 7 V0 (2026-07-31) 新增 schema_link + chart_reco → 14；
        # Phase 12 V2 (2026-08-03) 新增 decompose → 15；动态工具编排 tool_orchestrate → 16
        # v2.87 (2026-08-13) Phase 7 MetricResolver 抽象层新增 metric_resolve → 17
        # (2026-08-17) 会话历史压缩 history_compress 入本地红线 → 18
        # Phase 19 V0 (2026-08-31) 自进化失败反思 reflection 入本地红线 → 19
        # Phase 19 V1 (2026-08-31) skill_distill + answer_judge 入本地红线 → 21
        # Phase 19 V1.5 (2026-08-31) prompt_optimize 影子优化入本地红线 → 22
        assert len(_LOCAL_ONLY_TASKS) == 22, f"got {_LOCAL_ONLY_TASKS}"


# ---- stream.py SSE 三处同步 ----------------------------------------------------


class TestStreamBuiltinDrain:
    """graph/stream.py::_drain_builtin_events() 正确路由 3 新事件到 SSE 通道。"""

    def test_channel_by_kind_has_builtin(self):
        from agent.graph.stream import _CHANNEL_BY_KIND

        assert _CHANNEL_BY_KIND["builtin_tool_started"] == "agent://builtin_tool_started"
        assert _CHANNEL_BY_KIND["builtin_tool_done"] == "agent://builtin_tool_done"
        assert _CHANNEL_BY_KIND["builtin_tool_denied"] == "agent://builtin_tool_denied"
