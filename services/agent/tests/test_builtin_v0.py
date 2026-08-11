"""Phase 1B · V0 单元测试 + 集成测试（15 用例）。

覆盖:
  - path_sandbox: 5 攻击向量（路径穿越 / 符号链接 / Windows 保留名 / UNC / null byte）
  - builtin_read_file: 正常读取 + 100MB 超限
  - builtin_write_file: 原子写入 + overwrite=False
  - builtin_edit_file: search-replace 命中 + 0 匹配
  - builtin_list_dir: 正常列出
  - builtin_grep: ripgrep 走 OR Python 降级 + 100MB 超限
  - dispatcher: 端到端（tool_runner → builtin → audit → state）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# ---- path_sandbox 5 攻击向量 ----------------------------------------------------


class TestPathSandbox:
    """5 攻击向量必须 100% 拦截。"""

    def test_valid_path(self, tmp_path: Path):
        """普通文件路径走通。"""
        from agent.builtin.path_sandbox import validate_path

        p = tmp_path / "test.txt"
        p.write_text("hello")
        result = validate_path(str(p))
        assert result == p.resolve()

    def test_path_traversal_blocked(self, tmp_path: Path):
        """路径穿越 `..` 必须被 allowed_roots 拦截。"""
        from agent.builtin.models import PathOutOfBoundsError
        from agent.builtin.path_sandbox import validate_path

        outside = tmp_path.parent / "secret.txt"
        outside.write_text("secret")
        with pytest.raises(PathOutOfBoundsError):
            validate_path(str(outside), allowed_roots=[str(tmp_path)])

    def test_path_security_reserved_name(self, tmp_path: Path):
        """Windows 保留名（CON / PRN / NUL 等）必须拒绝。"""
        from agent.builtin.models import PathSecurityError
        from agent.builtin.path_sandbox import validate_path

        # 在 Windows 下强制 NUL 路径
        if sys.platform == "win32":
            with pytest.raises(PathSecurityError):
                validate_path("NUL")
        else:
            # Unix 下走 null byte 检查
            with pytest.raises(PathSecurityError):
                validate_path("path\x00with\x00null")

    def test_path_security_unc_blocked(self):
        """UNC 路径（\\\\server\\share）必须拒绝。"""
        from agent.builtin.models import PathSecurityError
        from agent.builtin.path_sandbox import validate_path

        with pytest.raises(PathSecurityError):
            validate_path("\\\\evil-server\\share")

    def test_path_security_null_byte(self):
        """null byte 注入必须拒绝。"""
        from agent.builtin.models import PathSecurityError
        from agent.builtin.path_sandbox import validate_path

        with pytest.raises(PathSecurityError):
            validate_path("path\x00injected")

    def test_path_security_empty(self):
        """空路径必须拒绝。"""
        from agent.builtin.models import PathSecurityError
        from agent.builtin.path_sandbox import validate_path

        with pytest.raises(PathSecurityError):
            validate_path("")

    def test_path_security_too_long(self):
        """超长路径必须拒绝。"""
        from agent.builtin.models import PathSecurityError
        from agent.builtin.path_sandbox import validate_path

        long_path = "a" * 5000
        with pytest.raises(PathSecurityError):
            validate_path(long_path)


# ---- builtin_read_file ----------------------------------------------------------


class TestBuiltinReadFile:
    """2 用例：正常读取 + 100MB 超限。"""

    async def test_read_normal(self, tmp_path: Path):
        from agent.builtin.files import builtin_read_file

        p = tmp_path / "hello.txt"
        p.write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = await builtin_read_file(str(p))
        assert result.ok
        assert result.content == "line1\nline2\nline3\n"
        assert result.meta["line_count"] == 3
        assert result.meta["size"] > 0

    async def test_read_line_range(self, tmp_path: Path):
        from agent.builtin.files import builtin_read_file

        p = tmp_path / "lines.txt"
        p.write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
        result = await builtin_read_file(str(p), start_line=10, max_lines=5)
        assert result.ok
        # 中间 5 行都用 \n 结尾（除最后一行 line99 外）
        assert result.content == "line10\nline11\nline12\nline13\nline14\n"
        assert result.meta["line_count"] == 5

    async def test_read_too_large(self, tmp_path: Path, monkeypatch):
        """100MB 超限：实际不写 100MB 文件，patch 限制。"""
        from agent.builtin import files as files_mod
        from agent.builtin.files import builtin_read_file

        # 临时把上限降到 1 byte
        monkeypatch.setattr(files_mod, "_MAX_FILE_BYTES", 1)
        p = tmp_path / "big.txt"
        p.write_text("x" * 100)
        result = await builtin_read_file(str(p))
        assert not result.ok
        assert "file_too_large" in result.error
        # V7：超限 hint 指向大文件只读工具（原为 logviewer）
        assert "builtin_log_read_lines" in result.hint


# ---- builtin_write_file ---------------------------------------------------------


class TestBuiltinWriteFile:
    """2 用例：原子写入 + overwrite=False。"""

    async def test_write_new_file(self, tmp_path: Path):
        from agent.builtin.files import builtin_write_file

        p = tmp_path / "new.txt"
        result = await builtin_write_file(str(p), "hello world")
        assert result.ok
        assert p.read_text() == "hello world"
        assert result.meta["bytes_written"] == 11
        assert result.needs_hitl  # medium 风险

    async def test_write_no_overwrite(self, tmp_path: Path):
        from agent.builtin.files import builtin_write_file

        p = tmp_path / "exists.txt"
        p.write_text("original")
        result = await builtin_write_file(str(p), "new content")
        assert not result.ok
        assert "file_exists" in result.error
        # 原文件未被覆盖
        assert p.read_text() == "original"


# ---- builtin_edit_file ----------------------------------------------------------


class TestBuiltinEditFile:
    """2 用例：search-replace 命中 + 0 匹配。"""

    async def test_edit_match(self, tmp_path: Path):
        from agent.builtin.files import builtin_edit_file

        p = tmp_path / "code.py"
        p.write_text("def foo():\n    return 1\n")
        result = await builtin_edit_file(str(p), "return 1", "return 42")
        assert result.ok
        assert result.meta["replacements"] == 1
        assert "return 42" in p.read_text()
        assert result.needs_hitl

    async def test_edit_no_match(self, tmp_path: Path):
        from agent.builtin.files import builtin_edit_file

        p = tmp_path / "code.py"
        p.write_text("hello world")
        result = await builtin_edit_file(str(p), "xxxxx", "yyyy")
        assert not result.ok
        assert "no_match" in result.error


# ---- builtin_list_dir -----------------------------------------------------------


class TestBuiltinListDir:
    """1 用例：正常列出。"""

    async def test_list_normal(self, tmp_path: Path):
        from agent.builtin.files import builtin_list_dir

        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("c")
        result = await builtin_list_dir(str(tmp_path))
        assert result.ok
        assert result.meta["count"] >= 3
        names = [e["name"] for e in result.content]
        assert "a.txt" in names
        assert "b.txt" in names
        assert "sub" in names


# ---- builtin_grep ---------------------------------------------------------------


class TestBuiltinGrep:
    """2 用例：文本匹配 + 100MB 超限。"""

    async def test_grep_match(self, tmp_path: Path):
        from agent.builtin.search import builtin_grep

        (tmp_path / "a.txt").write_text("hello\nworld\nfoo\n")
        (tmp_path / "b.txt").write_text("hello\neveryone\n")
        result = await builtin_grep("hello", str(tmp_path))
        assert result.ok
        assert result.meta["hit_count"] == 2
        # 至少一个命中的 file 字段含 basename
        assert any("a.txt" in h["file"] for h in result.content)

    async def test_grep_too_large(self, tmp_path: Path, monkeypatch):
        from agent.builtin import search as search_mod
        from agent.builtin.search import builtin_grep

        monkeypatch.setattr(search_mod, "_MAX_FILE_BYTES", 1)
        p = tmp_path / "big.txt"
        p.write_text("x" * 100)
        result = await builtin_grep("x", str(p))
        assert not result.ok
        assert "file_too_large" in result.error


# ---- ToolDispatcher 集成 ---------------------------------------------------------


class TestDispatcherIntegration:
    """1 用例：端到端（tool_runner → builtin → audit → state）。"""

    async def test_dispatch_builtin_read_file(self, tmp_path: Path, monkeypatch):
        """调度器调用 builtin_read_file,写 audit,返 state 增量。"""
        # 重定向 audit 到 tmp_path
        from agent.config import settings

        audit_db = tmp_path / "audit.sqlite"
        monkeypatch.setattr(settings, "audit_db_path", str(audit_db))

        # 准备测试文件
        test_file = tmp_path / "subject.txt"
        test_file.write_text("hello builtin")

        # 触发 dispatcher
        from agent.builtin.dispatcher import dispatcher as get_dispatcher
        from agent.builtin.dispatcher import reset_default_dispatcher

        reset_default_dispatcher()

        state = {"run_id": "test_run_1", "current_step_index": 0}
        call = {
            "server": "builtin",
            "name": "read_file",
            "args": {"path": str(test_file)},
        }
        result = await get_dispatcher().dispatch(call, state)

        # 验证 state 增量
        assert result is not None
        assert result["pending_tool_call"] == call
        tool_result = result["tool_result"]
        assert tool_result["ok"]
        assert tool_result["content"] == "hello builtin"
        assert tool_result["risk_level"] == "read"
        # trace 包含 name
        assert any("read_file" in str(e) for e in result["trace"])

        # 验证 audit 落库
        import json
        import sqlite3

        # 等异步 audit 落盘
        await asyncio.sleep(0.1)
        conn = sqlite3.connect(str(audit_db))
        rows = conn.execute(
            "SELECT action, payload FROM audit WHERE action='builtin_tool'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        payload = json.loads(rows[0][1])
        assert payload["name"] == "read_file"
        assert payload["ok"] is True
        assert payload["risk_level"] == "read"

    async def test_dispatch_unknown_tool(self):
        """未知工具返 error。"""
        from agent.builtin.dispatcher import dispatcher as get_dispatcher
        from agent.builtin.dispatcher import reset_default_dispatcher

        reset_default_dispatcher()

        state = {"run_id": "test_run_2"}
        call = {"server": "builtin", "name": "nonexistent", "args": {}}
        result = await get_dispatcher().dispatch(call, state)
        assert result is not None
        assert "unknown_builtin_tool" in result["tool_error"]

    async def test_dispatch_non_builtin_returns_none(self):
        """非 builtin call 返 None（让上游走 mcp）。"""
        from agent.builtin.dispatcher import dispatcher as get_dispatcher
        from agent.builtin.dispatcher import reset_default_dispatcher

        reset_default_dispatcher()

        state = {"run_id": "test_run_3"}
        call = {"server": "mcp", "name": "db_query", "args": {"sql": "SELECT 1"}}
        result = await get_dispatcher().dispatch(call, state)
        assert result is None
