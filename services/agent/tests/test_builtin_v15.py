"""Phase 1B V1.5 · Rust 工具 Tauri IPC 桥接测试（V2 后保留历史语义 + 新行为）。

覆盖:
  - tauri_bridge.is_v1_5_implemented() 识别 6 已实现 + 3 未实现（历史标记）
  - tauri_bridge.invoke_rust_tool_sync() runtime 不可用 → 返 None
  - dispatcher Rust 工具（无运行时 + 无 Python 兜底）→ rust_tool_not_implemented
  - dispatcher Rust 高危工具（delete_file / move_file / shell）→ V2 HITL 前置闸门
  - builtin_calculator / builtin_url_parse 等 Python 工具不受 bridge 影响
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


# ---- tauri_bridge 基础测试 --------------------------------------------------

class TestTauriBridgeBasics:
    """V1.5 tauri_bridge 协议测试。"""

    def test_v1_5_implemented_set_size(self):
        from agent.builtin.tauri_bridge import _V1_5_IMPLEMENTED_RUST_TOOLS
        assert len(_V1_5_IMPLEMENTED_RUST_TOOLS) == 6

    def test_v1_5_implemented_tools(self):
        from agent.builtin.tauri_bridge import is_v1_5_implemented
        for name in ("stat_file", "mkdir", "find", "glob", "hash", "base64"):
            assert is_v1_5_implemented(name), f"{name} should be V1.5 implemented"

    def test_v1_5_not_implemented(self):
        from agent.builtin.tauri_bridge import is_v1_5_implemented
        for name in ("delete_file", "move_file", "shell"):
            assert not is_v1_5_implemented(name), f"{name} should NOT be V1.5 implemented"

    @pytest.mark.asyncio
    async def test_invoke_v1_5_not_implemented_returns_none(self):
        """delete_file / move_file / shell（V2 已实现）无运行时 → 返 None（dispatcher 兜底）"""
        from agent.builtin.tauri_bridge import invoke_rust_tool_sync
        for name in ("delete_file", "move_file", "shell"):
            result = await invoke_rust_tool_sync(
                tool_name=name,
                args={"path": "/tmp/x"},
                risk_level="high",
            )
            assert result is None, f"{name} should return None (no runtime)"

    @pytest.mark.asyncio
    async def test_invoke_v1_5_implemented_no_runtime_returns_none(self):
        """V1.5 已实现的 6 工具在测试环境（无 Tauri runtime）→ 返 None"""
        from agent.builtin.tauri_bridge import invoke_rust_tool_sync
        for name in ("stat_file", "mkdir", "find", "glob", "hash", "base64"):
            result = await invoke_rust_tool_sync(
                tool_name=name,
                args={"path": "/tmp/test.txt"},
                risk_level="read",
            )
            # 测试环境无 Tauri runtime → 返 None
            assert result is None, f"{name} should return None (no runtime)"


# ---- dispatcher V1.5 集成测试 ------------------------------------------------

class TestDispatcherV15Integration:
    """V1.5 dispatcher 集成：Rust 工具走 bridge → runtime 不可用 → fallback。"""

    @pytest.mark.asyncio
    async def test_v1_5_rust_tool_python_fallback(self, tmp_path: Path):
        """stat_file 无运行时 → V3 Python 兜底直接执行（不再 not_implemented）。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.builtin._tauri_runtime import clear_tauri_runtime

        clear_tauri_runtime()
        reset_default_registry()
        target = tmp_path / "stat.txt"
        target.write_text("hello", encoding="utf-8")
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "stat_file", "args": {"path": str(target)}},
            {"run_id": "test-v15"},
        )
        assert result is not None
        assert result["tool_result"]["ok"] is True
        assert result["tool_result"]["content"]["size"] == 5

    @pytest.mark.asyncio
    async def test_v2_high_risk_tool_waits_for_approval(self):
        """delete_file V2 已实现 → 未审批时走 HITL 前置闸门（不执行）。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.builtin._tauri_runtime import clear_tauri_runtime

        clear_tauri_runtime()
        reset_default_registry()
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "delete_file", "args": {"path": "/tmp/test.txt"}},
            {"run_id": "test-v2-pending"},
        )
        assert result["awaiting_approval"] is True
        assert result["tool_result"] is None
        assert result["tool_error"] is None

    @pytest.mark.asyncio
    async def test_python_tool_unaffected_by_bridge(self):
        """Python 工具（calculator）不受 bridge 影响。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "calculator", "args": {"expression": "42+8"}},
            {"run_id": "test-py"},
        )
        assert result["tool_result"]["ok"] is True
        assert result["tool_result"]["content"]["value"] == 50

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_unknown_error(self):
        """未知工具仍返 unknown_builtin_tool（不被 bridge 兜底）。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "totally_fake_tool", "args": {}},
            {"run_id": "test-unknown"},
        )
        assert "unknown_builtin_tool" in result["tool_error"]

    @pytest.mark.asyncio
    async def test_rust_tool_python_fallback_writes_tool_calls_row(self, tmp_path: Path, monkeypatch):
        """Rust 工具 Python 兜底执行后写 tool_calls 表（保留审计完整性）。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry
        from agent.config import settings

        reset_default_registry()
        monkeypatch.setattr(settings, "audit_db_path", str(tmp_path / "audit.sqlite"))

        target = tmp_path / "hash.txt"
        target.write_text("abc", encoding="utf-8")
        result = await dispatcher().dispatch(
            {"server": "builtin", "name": "hash", "args": {"path": str(target), "algorithm": "sha256"}},
            {"run_id": "test-audit-v15"},
        )
        assert result["tool_result"]["ok"] is True

        # 检查 tool_calls 表
        import aiosqlite
        async with aiosqlite.connect(str(tmp_path / "audit.sqlite")) as db:
            cur = await db.execute(
                "SELECT tool_name, ok, error FROM tool_calls ORDER BY id DESC LIMIT 1"
            )
            row = await cur.fetchone()
        assert row is not None
        tool_name, ok, error = row
        assert tool_name == "hash"
        assert ok == 1


# ---- 公开 API 测试 --------------------------------------------------------

class TestV15PublicAPI:
    """V1.5 __init__.py 公开 API 测试。"""

    def test_tauri_bridge_exports(self):
        from agent.builtin import invoke_rust_tool_sync, is_rust_tool_v1_5_implemented
        assert callable(invoke_rust_tool_sync)
        assert callable(is_rust_tool_v1_5_implemented)

    def test_v1_5_helper_consistent(self):
        """is_rust_tool_v1_5_implemented 是 is_v1_5_implemented 的别名。"""
        from agent.builtin import is_rust_tool_v1_5_implemented
        from agent.builtin.tauri_bridge import is_v1_5_implemented
        assert is_rust_tool_v1_5_implemented is is_v1_5_implemented
