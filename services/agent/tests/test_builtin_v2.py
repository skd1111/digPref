"""Phase 1B V2 · 原生工具层收尾测试（30+ 用例）。

覆盖:
  - _tauri_runtime 注入协议（set / get / clear / is_available）
  - tauri_bridge V2：9 工具实现标记 + build_rust_args 映射 + 真实 invoke
    （超时重试 / require_hitl 透传 / 无运行时 → None）
  - 3 高危工具 Python 原生兜底（delete_file / move_file / shell 安全策略）
  - dispatcher HITL 前置闸门（审批前不执行 + 审批后放行 + 消费 approval_decision）
  - 无运行时 6 工具仍 not_implemented；审计行完整性
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

# ---- _tauri_runtime 注入协议 -------------------------------------------------


class TestTauriRuntimeInjection:
    def test_default_is_none(self):
        from agent.builtin._tauri_runtime import (
            clear_tauri_runtime,
            get_tauri_app_handle,
            is_tauri_runtime_available,
        )

        clear_tauri_runtime()
        assert get_tauri_app_handle() is None
        assert is_tauri_runtime_available() is False

    def test_set_get_clear(self):
        from agent.builtin._tauri_runtime import (
            clear_tauri_runtime,
            get_tauri_app_handle,
            is_tauri_runtime_available,
            set_tauri_runtime,
        )

        class Fake:
            async def invoke(self, command, args):
                return {}

        set_tauri_runtime(Fake())
        try:
            assert get_tauri_app_handle() is not None
            assert is_tauri_runtime_available() is True
        finally:
            clear_tauri_runtime()
        assert get_tauri_app_handle() is None

    def test_set_none_clears(self):
        from agent.builtin._tauri_runtime import (
            get_tauri_app_handle,
            set_tauri_runtime,
        )

        class Fake:
            async def invoke(self, command, args):
                return {}

        set_tauri_runtime(Fake())
        set_tauri_runtime(None)
        assert get_tauri_app_handle() is None


# ---- tauri_bridge V2 --------------------------------------------------------


class TestTauriBridgeV2:
    def test_implemented_sets(self):
        from agent.builtin.tauri_bridge import (
            _V1_5_IMPLEMENTED_RUST_TOOLS,
            _V2_IMPLEMENTED_RUST_TOOLS,
            has_python_fallback,
            is_implemented,
            is_v1_5_implemented,
            is_v2_implemented,
        )

        assert len(_V1_5_IMPLEMENTED_RUST_TOOLS) == 6
        assert len(_V2_IMPLEMENTED_RUST_TOOLS) == 9
        for name in ("stat_file", "mkdir", "find", "glob", "hash", "base64"):
            assert is_v1_5_implemented(name)
            assert is_v2_implemented(name)
            assert is_implemented(name)
        for name in ("delete_file", "move_file", "shell"):
            assert not is_v1_5_implemented(name)
            assert is_v2_implemented(name)
            assert is_implemented(name)
        assert has_python_fallback("delete_file")
        assert has_python_fallback("move_file")
        assert has_python_fallback("shell")
        # V3 (2026-08-03)：只读 Rust 工具补齐 Python 兜底，独立运行也可用
        for name in ("stat_file", "find", "glob", "hash", "base64"):
            assert has_python_fallback(name)
        assert not is_implemented("read_file")

    def test_build_rust_args_mapping(self):
        from agent.builtin.tauri_bridge import build_rust_args

        # 高危工具 require_hitl 透传
        args = build_rust_args(
            "delete_file",
            {"path": "/tmp/a", "recursive": True},
            require_hitl=True,
        )
        assert args == {
            "path": "/tmp/a",
            "recursive": True,
            "allowed_roots": [],
            "require_hitl": True,
        }
        args2 = build_rust_args(
            "shell",
            {"command": "echo hi", "allowed_prefixes": ["echo"], "timeout_sec": 5},
            require_hitl=False,
        )
        assert args2["command"] == "echo hi"
        assert args2["require_hitl"] is False
        assert args2["allowed_prefixes"] == ["echo"]
        assert args2["timeout_sec"] == 5
        # move_file / mkdir / hash
        args3 = build_rust_args("move_file", {"src": "a", "dest": "b"}, require_hitl=True)
        assert args3["src"] == "a"
        assert args3["dest"] == "b"
        assert args3["overwrite"] is False
        assert args3["require_hitl"] is True
        args4 = build_rust_args("hash", {"path": "x", "algorithm": "md5"})
        assert args4["algorithm"] == "md5"
        args5 = build_rust_args("mkdir", {"path": "d"})
        assert args5["parents"] is True
        assert args5["require_hitl"] is True

    @pytest.mark.asyncio
    async def test_invoke_no_runtime_returns_none(self):
        from agent.builtin._tauri_runtime import clear_tauri_runtime
        from agent.builtin.tauri_bridge import invoke_rust_tool_sync

        clear_tauri_runtime()
        for name in ("stat_file", "delete_file", "shell", "base64"):
            result = await invoke_rust_tool_sync(
                tool_name=name,
                args={"path": "/tmp/x"},
                risk_level="read",
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_invoke_with_fake_runtime_success(self):
        from agent.builtin._tauri_runtime import clear_tauri_runtime, set_tauri_runtime
        from agent.builtin.tauri_bridge import invoke_rust_tool_sync

        class FakeRuntime:
            def __init__(self):
                self.calls = []

            async def invoke(self, command, args):
                self.calls.append((command, args))
                return {
                    "ok": True,
                    "content": {"size": 11},
                    "meta": {},
                    "needs_hitl": False,
                    "risk_level": "read",
                }

        fake = FakeRuntime()
        set_tauri_runtime(fake)
        try:
            result = await invoke_rust_tool_sync(
                tool_name="stat_file",
                args={"path": "/tmp/f.txt"},
                risk_level="read",
            )
        finally:
            clear_tauri_runtime()
        assert result is not None
        assert result.ok is True
        assert result.content == {"size": 11}
        assert result.risk_level == "read"
        assert fake.calls[0][0] == "builtin_stat_file"
        assert fake.calls[0][1]["path"] == "/tmp/f.txt"

    @pytest.mark.asyncio
    async def test_invoke_timeout_then_retry_success(self):
        from agent.builtin._tauri_runtime import clear_tauri_runtime, set_tauri_runtime
        from agent.builtin.tauri_bridge import invoke_rust_tool_sync

        class FlakyRuntime:
            def __init__(self):
                self.attempts = 0

            async def invoke(self, command, args):
                self.attempts += 1
                if self.attempts == 1:
                    raise asyncio.TimeoutError("ipc timeout")
                return {
                    "ok": True,
                    "content": "retried",
                    "meta": {},
                    "needs_hitl": False,
                    "risk_level": "read",
                }

        flaky = FlakyRuntime()
        set_tauri_runtime(flaky)
        try:
            result = await invoke_rust_tool_sync(
                tool_name="base64",
                args={"data": "x", "mode": "encode"},
                risk_level="read",
                timeout_sec=0.2,
            )
        finally:
            clear_tauri_runtime()
        assert result is not None
        assert result.content == "retried"
        assert flaky.attempts == 2

    @pytest.mark.asyncio
    async def test_invoke_timeout_both_fail_returns_none(self):
        from agent.builtin._tauri_runtime import clear_tauri_runtime, set_tauri_runtime
        from agent.builtin.tauri_bridge import invoke_rust_tool_sync

        class AlwaysTimeout:
            async def invoke(self, command, args):
                raise asyncio.TimeoutError("always")

        set_tauri_runtime(AlwaysTimeout())
        try:
            result = await invoke_rust_tool_sync(
                tool_name="hash",
                args={"path": "/tmp/x", "algorithm": "sha256"},
                risk_level="read",
                timeout_sec=0.1,
            )
        finally:
            clear_tauri_runtime()
        assert result is None

    @pytest.mark.asyncio
    async def test_invoke_require_hitl_passthrough(self):
        from agent.builtin._tauri_runtime import clear_tauri_runtime, set_tauri_runtime
        from agent.builtin.tauri_bridge import invoke_rust_tool_sync

        class RecordingRuntime:
            def __init__(self):
                self.seen = None

            async def invoke(self, command, args):
                self.seen = args
                return {
                    "ok": False,
                    "error": "hitl_required",
                    "hint": "approve first",
                    "meta": {},
                    "needs_hitl": True,
                    "risk_level": "high",
                }

        fake = RecordingRuntime()
        set_tauri_runtime(fake)
        try:
            result = await invoke_rust_tool_sync(
                tool_name="delete_file",
                args={"path": "/tmp/x"},
                risk_level="high",
                require_hitl=False,
            )
        finally:
            clear_tauri_runtime()
        assert fake.seen["require_hitl"] is False
        assert result is not None
        assert result.needs_hitl is True
        assert result.error == "hitl_required"


# ---- 3 高危工具 Python 原生兜底 ---------------------------------------------


class TestPythonFallbackDelete:
    @pytest.mark.asyncio
    async def test_delete_file_ok(self, tmp_path: Path):
        from agent.builtin.files import builtin_delete_file

        f = tmp_path / "bye.txt"
        f.write_text("x")
        r = await builtin_delete_file(str(f))
        assert r.ok
        assert r.content["removed"] is True
        assert not f.exists()
        assert r.risk_level == "high"

    @pytest.mark.asyncio
    async def test_delete_dir_requires_recursive(self, tmp_path: Path):
        from agent.builtin.files import builtin_delete_file

        d = tmp_path / "sub"
        d.mkdir()
        r = await builtin_delete_file(str(d))
        assert not r.ok
        assert "is_directory" in r.error
        assert d.exists()

    @pytest.mark.asyncio
    async def test_delete_dir_recursive(self, tmp_path: Path):
        from agent.builtin.files import builtin_delete_file

        d = tmp_path / "sub"
        d.mkdir()
        (d / "inner.txt").write_text("x")
        r = await builtin_delete_file(str(d), recursive=True)
        assert r.ok
        assert not d.exists()

    @pytest.mark.asyncio
    async def test_delete_root_forbidden(self, tmp_path: Path):
        from agent.builtin.files import builtin_delete_file

        r = await builtin_delete_file(str(tmp_path), recursive=True, allowed_roots=[str(tmp_path)])
        assert not r.ok
        assert "delete_root_forbidden" in r.error
        assert tmp_path.exists()

    @pytest.mark.asyncio
    async def test_delete_out_of_bounds(self, tmp_path: Path):
        from agent.builtin.files import builtin_delete_file

        outside = tmp_path.parent / "secret_v2.txt"
        outside.write_text("s")
        try:
            r = await builtin_delete_file(str(outside), allowed_roots=[str(tmp_path)])
            assert not r.ok
            assert "PathOutOfBoundsError" in r.error
            assert outside.exists()
        finally:
            if outside.exists():
                outside.unlink()


class TestPythonFallbackMove:
    @pytest.mark.asyncio
    async def test_move_ok(self, tmp_path: Path):
        from agent.builtin.files import builtin_move_file

        src = tmp_path / "a.txt"
        dest = tmp_path / "b.txt"
        src.write_text("hello")
        r = await builtin_move_file(str(src), str(dest))
        assert r.ok
        assert not src.exists()
        assert dest.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_move_no_overwrite(self, tmp_path: Path):
        from agent.builtin.files import builtin_move_file

        src = tmp_path / "a.txt"
        dest = tmp_path / "b.txt"
        src.write_text("new")
        dest.write_text("old")
        r = await builtin_move_file(str(src), str(dest))
        assert not r.ok
        assert "dest_exists" in r.error
        assert dest.read_text() == "old"

    @pytest.mark.asyncio
    async def test_move_overwrite(self, tmp_path: Path):
        from agent.builtin.files import builtin_move_file

        src = tmp_path / "a.txt"
        dest = tmp_path / "b.txt"
        src.write_text("new")
        dest.write_text("old")
        r = await builtin_move_file(str(src), str(dest), overwrite=True)
        assert r.ok
        assert dest.read_text() == "new"

    @pytest.mark.asyncio
    async def test_move_dir(self, tmp_path: Path):
        from agent.builtin.files import builtin_move_file

        src = tmp_path / "dir_a"
        dest = tmp_path / "dir_b"
        src.mkdir()
        (src / "f.txt").write_text("x")
        r = await builtin_move_file(str(src), str(dest))
        assert r.ok
        assert (dest / "f.txt").exists()


class TestPythonFallbackShell:
    @pytest.mark.asyncio
    async def test_echo_ok(self):
        from agent.builtin.shell import builtin_shell

        # 2026-08-27：Windows 上默认 shell 改为 pwsh（装了才用），而 pwsh 的
        # `echo a b` 是两个参数 → 输出两行；cmd 是一整串 → 一行。这里显式加引号
        # 保证跨 shell 行为一致（这条差异已写进工具描述，模型能看到）。
        r = await builtin_shell('echo "hello v2"', allowed_prefixes=["echo"])
        assert r.ok, r.error
        assert r.content["exit_code"] == 0
        assert "hello v2" in r.content["stdout"]
        assert r.content["timed_out"] is False
        assert r.risk_level == "critical"
        assert r.content["shell"] in ("pwsh", "cmd", "sh")

    @pytest.mark.asyncio
    async def test_result_reports_which_shell(self):
        """模型必须知道自己在跟哪个 shell 说话 —— pwsh 的 where/dir/type 都是别名，
        语法与 cmd 有实质差异（BUGFIX #165 的相邻风险）。"""
        from agent.builtin.shell import builtin_shell, current_shell_name, shell_syntax_note

        r = await builtin_shell('echo "x"', allowed_prefixes=["echo"])
        assert r.content["shell"] == current_shell_name()
        assert shell_syntax_note(), "每种 shell 都要有写法提醒（注入工具描述）"

    @pytest.mark.asyncio
    async def test_shell_note_reaches_tool_description(self):
        """语法提醒必须真的进 LLM system prompt，否则等于没说。"""
        from agent.builtin.registry import get_default_registry
        from agent.builtin.shell import shell_syntax_note

        desc = get_default_registry().generate_tool_descriptions()
        shell_line = next(li for li in desc.splitlines() if li.startswith("- builtin_shell:"))
        assert shell_syntax_note() in shell_line

    @pytest.mark.asyncio
    async def test_blocks_metacharacters(self):
        from agent.builtin.shell import builtin_shell

        for cmd in (
            "echo hi; rm -rf /",
            "echo hi && rm -rf /",
            "echo hi | sh",
            "echo `id`",
            "echo $(id)",
            "echo hi > /tmp/x",
        ):
            r = await builtin_shell(cmd, allowed_prefixes=["echo"])
            assert not r.ok, f"should block: {cmd}"
            assert "dangerous_operator" in r.error

    @pytest.mark.asyncio
    async def test_command_not_allowed(self):
        from agent.builtin.shell import builtin_shell

        r = await builtin_shell("rm -rf x", allowed_prefixes=["echo"])
        assert not r.ok
        assert "command_not_allowed" in r.error

    @pytest.mark.asyncio
    async def test_empty_command(self):
        from agent.builtin.shell import builtin_shell

        r = await builtin_shell("   ")
        assert not r.ok
        assert "empty_command" in r.error

    @pytest.mark.asyncio
    async def test_timeout_kills(self):
        import sys as _sys

        from agent.builtin.shell import builtin_shell

        if _sys.platform == "win32":
            cmd = "ping -n 6 127.0.0.1"
        else:
            cmd = "sleep 5"
        r = await builtin_shell(cmd, allowed_prefixes=[cmd.split(" ")[0]], timeout_sec=1)
        # 根治 BUGFIX #165：超时是失败 —— 被强杀的命令没有产出。
        # 此前断言 r.ok（"进程成功启动"语义），让 tools/loop.py 的停滞熔断看不见空转。
        assert not r.ok, "timeout must be reported as failure"
        assert "timeout" in (r.error or "")
        assert r.content["timed_out"] is True
        assert r.content["exit_code"] == 124

    # ---- 根治 BUGFIX #165：ok 语义 = 命令达成目标 ----------------------------

    @pytest.mark.asyncio
    async def test_nonzero_exit_is_failure(self):
        """核心回归：命令跑了但失败 → ok=False，真实原因进 error。

        此前无条件 ok=True（"进程成功启动"语义），退出码埋在 content 里。
        后果：tools/loop.py 的停滞熔断（连续 3 轮零成功 → 掐断）计数器一次都涨不
        起来，模型对着同一条失败命令换了 22 种写法，把 24 轮编排预算烧光。
        """
        from agent.builtin.shell import builtin_shell

        r = await builtin_shell("exit 3", allowed_prefixes=["exit"])
        assert not r.ok, "nonzero exit must be failure"
        assert "exit_code=3" in (r.error or "")
        assert r.content is not None, "失败也要保留 content 供模型读 stdout/stderr"
        assert r.content["exit_code"] == 3

    @pytest.mark.asyncio
    async def test_allow_nonzero_exit_opt_in(self):
        """findstr / grep 无匹配返 1、diff 有差异返 1 —— 显式放行。"""
        from agent.builtin.shell import builtin_shell

        r = await builtin_shell("exit 3", allowed_prefixes=["exit"], allow_nonzero_exit=True)
        assert r.ok, r.error
        assert r.content["exit_code"] == 3

    @pytest.mark.asyncio
    async def test_unknown_command_reports_failure_with_reason(self):
        """命令不存在时 error 必须带真实原因，而不是让模型去 content.stderr 里翻。"""
        from agent.builtin.shell import builtin_shell

        r = await builtin_shell(
            "definitely-not-a-real-command-xyz",
            allowed_prefixes=["definitely-not-a-real-command-xyz"],
        )
        assert not r.ok
        assert "exit_code=" in (r.error or "")

    @pytest.mark.asyncio
    async def test_block_hints_are_actionable(self):
        """拦截必须给出路，否则模型盲试到预算耗尽（BUGFIX #165 的推手）。"""
        from agent.builtin.shell import builtin_shell

        r = await builtin_shell("echo a && echo b", allowed_prefixes=["echo"])
        assert not r.ok
        assert r.hint and "builtin_shell" in r.hint, f"应提示拆分调用: {r.hint}"

        r2 = await builtin_shell("echo a | findstr b", allowed_prefixes=["echo"])
        assert r2.hint and "builtin_grep" in r2.hint

        # cmd 的 `if exist (...)` 是那次事故里的真实写法之一
        r3 = await builtin_shell(r'if exist "C:\x" (echo y)', allowed_prefixes=["if"])
        assert r3.hint and "builtin_stat_file" in r3.hint

        r4 = await builtin_shell("dir x", allowed_prefixes=["echo"])
        assert r4.hint and "builtin_list_dir" in r4.hint, "白名单拦截也要带 hint"

    @pytest.mark.asyncio
    async def test_windows_prefers_pwsh_when_available(self):
        """Windows 上装了 pwsh 就用 pwsh（引号规则比 cmd 一致），否则回退 cmd。"""
        import sys as _sys

        if _sys.platform != "win32":
            pytest.skip("Windows-only shell 选择逻辑")
        from agent.builtin import shell as shell_mod

        argv = shell_mod._win_shell_argv("echo hi")
        if shell_mod._WIN_PWSH_PATH:
            assert "pwsh" in argv[0].lower()
            assert "-NoProfile" in argv and "-NonInteractive" in argv
        else:
            assert argv[:2] == ["cmd", "/C"]


# ---- dispatcher HITL 前置闸门 ------------------------------------------------


class TestDispatcherHitlGateV2:
    @pytest.mark.asyncio
    async def test_delete_file_waits_for_approval(self, tmp_path: Path, monkeypatch):
        """高危工具未审批 → 不执行、返 awaiting_approval=True。"""
        from agent.builtin.dispatcher import dispatcher, reset_default_dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.config import settings

        reset_default_registry()
        reset_default_dispatcher()
        monkeypatch.setattr(settings, "audit_db_path", str(tmp_path / "audit.sqlite"))
        f = tmp_path / "target.txt"
        f.write_text("do not delete")

        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "delete_file", "args": {"path": str(f)}},
            {"run_id": "v2-hitl"},
        )
        assert result["awaiting_approval"] is True
        assert result["tool_result"] is None
        assert result["tool_error"] is None
        assert f.exists(), "file must NOT be deleted before approval"

    @pytest.mark.asyncio
    async def test_delete_file_executes_after_approval(self, tmp_path: Path, monkeypatch):
        """审批通过（approval_decision=approve）→ 执行并消费 approval_decision。"""
        from agent.builtin.dispatcher import dispatcher, reset_default_dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.config import settings

        reset_default_registry()
        reset_default_dispatcher()
        monkeypatch.setattr(settings, "audit_db_path", str(tmp_path / "audit.sqlite"))
        f = tmp_path / "target.txt"
        f.write_text("delete me")

        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "delete_file", "args": {"path": str(f)}},
            {"run_id": "v2-approved", "approval_decision": "approve"},
        )
        assert result["tool_result"]["ok"] is True
        assert result["tool_result"]["content"]["removed"] is True
        assert not f.exists()
        assert result["awaiting_approval"] is False
        # 消费 approval_decision，防止放行后续高危调用
        assert result["approval_decision"] is None

    @pytest.mark.asyncio
    async def test_shell_waits_for_approval_even_without_flag(self):
        """critical 工具永远等审批（即使全局开关关）。"""
        from agent.builtin.dispatcher import dispatcher, reset_default_dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.config import settings

        reset_default_registry()
        reset_default_dispatcher()
        original = settings.require_hitl_for_write
        settings.require_hitl_for_write = False
        try:
            result = await dispatcher().dispatch(
                {"server": "builtin", "name": "shell", "args": {"command": "echo hi"}},
                {"run_id": "v2-shell"},
            )
        finally:
            settings.require_hitl_for_write = original
        assert result["awaiting_approval"] is True
        assert result["tool_result"] is None

    @pytest.mark.asyncio
    async def test_shell_executes_after_approval(self):
        """shell 审批后执行（无运行时 → Python 兜底）。"""
        from agent.builtin.dispatcher import dispatcher, reset_default_dispatcher
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        reset_default_dispatcher()
        result = await dispatcher().dispatch(
            {
                "server": "builtin",
                "name": "shell",
                "args": {"command": "echo approved", "allowed_prefixes": ["echo"]},
            },
            {"run_id": "v2-shell-ok", "approval_decision": "approve"},
        )
        assert result["tool_result"]["ok"] is True
        assert "approved" in result["tool_result"]["content"]["stdout"]
        assert result["approval_decision"] is None

    @pytest.mark.asyncio
    async def test_write_file_waits_for_approval(self, tmp_path: Path):
        """回归：write_file（medium）审批前不落盘。"""
        from agent.builtin.dispatcher import dispatcher, reset_default_dispatcher
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        reset_default_dispatcher()
        f = tmp_path / "gated.txt"
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "write_file", "args": {"path": str(f), "content": "x"}},
            {"run_id": "v2-write-gate"},
        )
        assert result["awaiting_approval"] is True
        assert not f.exists(), "write must not happen before approval"

    @pytest.mark.asyncio
    async def test_stat_file_python_fallback_without_runtime(self, tmp_path: Path):
        """无运行时 → stat_file 走 V3 Python 兜底执行。"""
        from agent.builtin._tauri_runtime import clear_tauri_runtime
        from agent.builtin.dispatcher import dispatcher, reset_default_dispatcher
        from agent.builtin.registry import reset_default_registry

        clear_tauri_runtime()
        reset_default_registry()
        reset_default_dispatcher()
        target = tmp_path / "v2-stat.txt"
        target.write_text("data", encoding="utf-8")
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "stat_file", "args": {"path": str(target)}},
            {"run_id": "v2-stat"},
        )
        assert result["tool_result"]["ok"] is True
        assert result["tool_result"]["content"]["size"] == 4

    @pytest.mark.asyncio
    async def test_calculator_unaffected(self):
        """低风险 Python 工具不受 HITL 闸门影响。"""
        from agent.builtin.dispatcher import dispatcher, reset_default_dispatcher
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        reset_default_dispatcher()
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "calculator", "args": {"expression": "1+1"}},
            {"run_id": "v2-calc"},
        )
        assert result["tool_result"]["ok"] is True
        assert result["tool_result"]["content"]["value"] == 2
        assert result["awaiting_approval"] is False

    @pytest.mark.asyncio
    async def test_approved_delete_writes_audit_row(self, tmp_path: Path, monkeypatch):
        """审批后执行写 tool_calls 审计行。"""
        from agent.builtin.dispatcher import dispatcher, reset_default_dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.config import settings

        reset_default_registry()
        reset_default_dispatcher()
        monkeypatch.setattr(settings, "audit_db_path", str(tmp_path / "audit.sqlite"))
        f = tmp_path / "audited.txt"
        f.write_text("x")

        await dispatcher().dispatch(
            {"server": "builtin", "name": "delete_file", "args": {"path": str(f)}},
            {"run_id": "v2-audit", "approval_decision": "approve"},
        )
        await asyncio.sleep(0.15)
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "audit.sqlite"))
        row = conn.execute(
            "SELECT tool_name, ok, needs_hitl FROM tool_calls ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "delete_file"
        assert row[1] == 1
        assert row[2] == 1  # needs_hitl 记录（审批通过执行）


# ---- V2 公开 API ------------------------------------------------------------


class TestV2PublicAPI:
    def test_v2_exports(self):
        from agent.builtin import (
            builtin_delete_file,
            builtin_move_file,
            builtin_shell,
            has_python_fallback,
            is_rust_tool_implemented,
            is_rust_tool_v1_5_implemented,
            is_rust_tool_v2_implemented,
            is_tauri_runtime_available,
            set_tauri_runtime,
        )

        assert callable(builtin_delete_file)
        assert callable(builtin_move_file)
        assert callable(builtin_shell)
        assert callable(set_tauri_runtime)
        assert callable(is_rust_tool_implemented)
        assert callable(is_rust_tool_v1_5_implemented)
        assert callable(is_rust_tool_v2_implemented)
        assert callable(has_python_fallback)
        assert callable(is_tauri_runtime_available)
        assert is_rust_tool_v2_implemented("shell")
        assert has_python_fallback("delete_file")

    def test_v1_5_alias_still_works(self):
        from agent.builtin import is_rust_tool_v1_5_implemented
        from agent.builtin.tauri_bridge import is_v1_5_implemented

        assert is_rust_tool_v1_5_implemented is is_v1_5_implemented


# ---- 根治 BUGFIX #166：argv 免引号路径 + cwd + glob 参数名 ------------------


class TestShellArgvForm:
    """argv 数组形式 —— 绕过 shell 引号规则。

    背景：模型为了调用一个路径含空格的 python.exe 连试 22 轮 —— cmd 下直接调用
    不成立，pwsh 下唯一正确的 `& "路径"` 写法又被危险操作符拦截，被逼进死角。
    argv 直接 exec，没有引号 / 转义 / 操作符，是唯一可靠路径。
    """

    @pytest.mark.asyncio
    async def test_argv_runs_interpreter_with_spaces_in_path(self):
        import sys as _sys

        from agent.builtin.shell import builtin_shell

        r = await builtin_shell(argv=[_sys.executable, "-c", "print('argv-ok')"])
        assert r.ok, r.error
        assert "argv-ok" in r.content["stdout"]
        assert r.content["shell"] == "none", "argv 不经 shell"
        assert r.content["argv"][0] == _sys.executable

    @pytest.mark.asyncio
    async def test_argv_skips_dangerous_operator_check(self):
        """argv 元素不会被 shell 解释 → 含 & 的字面量参数不该被拦。"""
        import sys as _sys

        from agent.builtin.shell import builtin_shell

        r = await builtin_shell(argv=[_sys.executable, "-c", "print('a&b|c;d')"])
        assert r.ok, r.error
        assert "a&b|c;d" in r.content["stdout"]

    @pytest.mark.asyncio
    async def test_argv_whitelist_uses_first_element(self):
        import sys as _sys

        from agent.builtin.shell import builtin_shell

        r = await builtin_shell(argv=[_sys.executable, "-c", "pass"], allowed_prefixes=["nope"])
        assert not r.ok
        assert "command_not_allowed" in r.error

    @pytest.mark.asyncio
    async def test_argv_rejects_non_string_elements(self):
        from agent.builtin.shell import builtin_shell

        r = await builtin_shell(argv=["echo", 123])  # type: ignore[list-item]
        assert not r.ok
        assert "invalid_argv" in r.error

    @pytest.mark.asyncio
    async def test_empty_argv_and_empty_command(self):
        from agent.builtin.shell import builtin_shell

        assert "empty_command" in ((await builtin_shell(argv=[""])).error or "")
        r = await builtin_shell("")
        assert "empty_command" in (r.error or "")
        assert "argv" in (r.hint or ""), "空命令时应推荐 argv 形式"

    @pytest.mark.asyncio
    async def test_cwd_takes_effect(self):
        """cd 不跨调用生效，切目录必须用 cwd（实测模型在此白花 3 轮）。"""
        import sys as _sys
        import tempfile
        from pathlib import Path as _Path

        from agent.builtin.shell import builtin_shell

        with tempfile.TemporaryDirectory() as td:
            r = await builtin_shell(
                argv=[_sys.executable, "-c", "import os;print(os.getcwd())"], cwd=td
            )
            assert r.ok, r.error
            assert _Path(r.content["stdout"].strip()).resolve() == _Path(td).resolve()
            assert r.content["cwd"]

    @pytest.mark.asyncio
    async def test_cwd_must_exist(self):
        import sys as _sys

        from agent.builtin.shell import builtin_shell

        r = await builtin_shell(argv=[_sys.executable, "-c", "pass"], cwd="/nope/xyz/123")
        assert not r.ok
        assert "cwd_not_a_directory" in r.error


class TestShellRobustness:
    @pytest.mark.asyncio
    async def test_windows_path_first_token_not_mangled(self):
        r"""shlex POSIX 模式会啃掉 Windows 反斜杠 → 正确的白名单也被判违规。

        输入  C:\Users\x\Enterprise AI IDE\python.exe
        posix=True  → 'C:UsersxEnterprise'   ← 审计里的真实报错
        posix=False → 'C:\Users\x\Enterprise'
        """
        import sys as _sys

        from agent.builtin.shell import _first_token

        if _sys.platform != "win32":
            pytest.skip("Windows 路径分词专项")
        raw = r"C:\Users\79834\AppData\Local\Enterprise AI IDE\python.exe --version"
        assert "\\" in _first_token(raw), "反斜杠不得被吞掉"
        # 带引号的完整路径应被完整取出且剥掉引号
        quoted = r'"C:\Program Files\python.exe" script.py'
        assert _first_token(quoted).startswith(r"C:\Program Files")

    @pytest.mark.asyncio
    async def test_unbalanced_quotes_do_not_crash(self):
        from agent.builtin.shell import _first_token

        assert _first_token('echo "unclosed') == "echo"

    def test_strip_ansi_removes_pwsh_colour_codes(self):
        """pwsh 彩色报错会把 [31;1m 混进 stderr，挤占 error 的有效信息。"""
        from agent.builtin.shell import _strip_ansi

        assert _strip_ansi("[31;1mResourceUnavailable[0m: x") == "ResourceUnavailable: x"
        assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"
        assert _strip_ansi("array[0] = x") == "array[0] = x", "普通方括号不能被吃掉"

    def test_stderr_digest_is_ansi_free(self):
        from agent.builtin.shell import _stderr_digest

        assert "[31" not in _stderr_digest("[31;1mboom[0m", "")


class TestGlobArgAlias:
    """glob 参数名 root / base_dir 互为别名（BUGFIX #166）。

    schema 对模型声明 base_dir（可选，默认 "."），Rust GlobArgs 却叫 root 且无默认
    → 模型按 schema 传参时拿到空串，validate_path("") 报 empty path，
    glob 工具**永远不可用**。实测模型三次尝试 glob 全被打回，只能退回 shell。
    """

    def test_build_rust_args_accepts_base_dir(self):
        from agent.builtin.tauri_bridge import build_rust_args

        out = build_rust_args("glob", {"pattern": "**/*.py", "base_dir": "/tmp"})
        assert out["root"] == "/tmp"

    def test_build_rust_args_accepts_root(self):
        from agent.builtin.tauri_bridge import build_rust_args

        out = build_rust_args("glob", {"pattern": "**/*.py", "root": "/tmp"})
        assert out["root"] == "/tmp"

    def test_build_rust_args_defaults_to_cwd_not_empty(self):
        """缺省必须回落 "."，绝不能是空串 —— 空串会被 path 沙箱判 empty path。"""
        from agent.builtin.tauri_bridge import build_rust_args

        out = build_rust_args("glob", {"pattern": "**/*.py"})
        assert out["root"] == ".", f"缺省应为 '.'，实际 {out['root']!r}"

    def test_python_fallback_accepts_both_names(self):
        from agent.builtin.fallbacks import builtin_glob_py

        assert builtin_glob_py(pattern="*", base_dir=".").ok
        assert builtin_glob_py(pattern="*", root=".").ok

    def test_python_fallback_tolerates_rust_only_args(self):
        """dispatcher 直接 **args 展开时不能因 Rust 专用参数抛 TypeError。"""
        from agent.builtin.fallbacks import builtin_glob_py

        assert builtin_glob_py(pattern="*", root=".", max_results=10, allowed_roots=[]).ok


class TestShellArgvPassThrough:
    def test_build_rust_args_forwards_argv_and_cwd(self):
        """桌面端走 Rust —— 新参数不透传等于没修（BUGFIX #165/#166）。"""
        from agent.builtin.tauri_bridge import build_rust_args

        out = build_rust_args(
            "shell",
            {
                "argv": ["python", "x.py"],
                "cwd": "/tmp",
                "allow_nonzero_exit": True,
                "timeout_sec": 5,
            },
        )
        assert out["argv"] == ["python", "x.py"]
        assert out["cwd"] == "/tmp"
        assert out["allow_nonzero_exit"] is True
        assert out["timeout_sec"] == 5

    def test_tool_description_advertises_argv_and_cwd(self):
        """模型只看工具描述 —— argv / cwd 不写进去等于不存在。"""
        from agent.builtin.registry import get_default_registry

        desc = get_default_registry().generate_tool_descriptions()
        line = next(li for li in desc.splitlines() if li.startswith("- builtin_shell:"))
        assert "argv" in line
        assert "cwd" in line
