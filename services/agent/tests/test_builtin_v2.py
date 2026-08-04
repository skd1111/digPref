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
import sys
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
                return {"ok": True, "content": "retried", "meta": {}, "needs_hitl": False, "risk_level": "read"}

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
                return {"ok": False, "error": "hitl_required", "hint": "approve first",
                        "meta": {}, "needs_hitl": True, "risk_level": "high"}

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
        r = await builtin_shell("echo hello v2", allowed_prefixes=["echo"])
        assert r.ok, r.error
        assert r.content["exit_code"] == 0
        assert "hello v2" in r.content["stdout"]
        assert r.content["timed_out"] is False
        assert r.risk_level == "critical"

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
        from agent.builtin.shell import builtin_shell
        import sys as _sys
        if _sys.platform == "win32":
            cmd = "ping -n 6 127.0.0.1"
        else:
            cmd = "sleep 5"
        r = await builtin_shell(cmd, allowed_prefixes=[cmd.split(" ")[0]], timeout_sec=1)
        assert r.ok, r.error
        assert r.content["timed_out"] is True
        assert r.content["exit_code"] == 124


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
            {"server": "builtin", "name": "shell",
             "args": {"command": "echo approved", "allowed_prefixes": ["echo"]}},
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
            {"server": "builtin", "name": "write_file",
             "args": {"path": str(f), "content": "x"}},
            {"run_id": "v2-write-gate"},
        )
        assert result["awaiting_approval"] is True
        assert not f.exists(), "write must not happen before approval"

    @pytest.mark.asyncio
    async def test_stat_file_python_fallback_without_runtime(self, tmp_path: Path):
        """无运行时 → stat_file 走 V3 Python 兜底执行。"""
        from agent.builtin.dispatcher import dispatcher, reset_default_dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.builtin._tauri_runtime import clear_tauri_runtime

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
