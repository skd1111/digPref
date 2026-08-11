"""Phase 1B V7 大文件查看与搜索测试（klogg 式只读，2026-08-10）。

覆盖：
    - log_read_lines（范围读取 / 早停 / EOF meta / tail 模式 / 尾换行边界 / 超长行截断）
    - log_search（字面量 / 正则 / 忽略大小写 / 上下文 / 上限截断 / 早停 / 错误路径）
    - 只读保证：调用前后文件内容哈希不变
    - 登记完整性 + dispatcher read 直通
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from agent.builtin.logfile import builtin_log_read_lines, builtin_log_search
from agent.builtin.models import BUILTIN_TOOL_NAMES
from agent.builtin.registry import TOOL_DESCRIPTIONS, TOOL_RISK_LEVEL, get_default_registry
from agent.builtin.schemas import get_builtin_schema

_TOTAL_LINES = 10_000


@pytest.fixture
def log_file(tmp_path: Path) -> Path:
    """1 万行日志：每 1000 行一个 ERROR 块（带 2 行堆栈）。"""
    p = tmp_path / "app.log"
    with p.open("w", encoding="utf-8") as fh:
        for i in range(_TOTAL_LINES):
            if i % 1000 == 500:
                fh.write(f"2026-08-10 ERROR boom-{i}\n")
                fh.write(f"    at com.example.Foo.bar(Foo.java:{i})\n")
                fh.write("    Caused by: java.lang.NullPointerException\n")
            else:
                fh.write(f"2026-08-10 INFO line-{i}\n")
    return p


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---- log_read_lines --------------------------------------------------------------


class TestLogReadLines:
    def test_range_read(self, log_file):
        result = builtin_log_read_lines(path=str(log_file), start_line=100, max_lines=10)
        assert result.ok is True
        assert len(result.content) == 10
        assert "line-100" in result.content[0]
        assert "line-109" in result.content[9]
        assert result.meta["reached_eof"] is False

    def test_read_to_eof_meta(self, tmp_path):
        small = tmp_path / "small.log"
        small.write_text("a\nb\nc\n", encoding="utf-8")
        result = builtin_log_read_lines(path=str(small), max_lines=100)
        assert result.ok is True
        assert result.content == ["a", "b", "c"]
        assert result.meta["reached_eof"] is True
        assert result.meta["line_count"] == 3

    def test_read_beyond_eof(self, log_file):
        result = builtin_log_read_lines(path=str(log_file), start_line=10_000_000, max_lines=5)
        assert result.ok is True
        assert result.content == []
        assert result.meta["reached_eof"] is True

    def test_empty_file(self, tmp_path):
        empty = tmp_path / "empty.log"
        empty.write_text("", encoding="utf-8")
        result = builtin_log_read_lines(path=str(empty))
        assert result.ok is True
        assert result.content == []
        assert result.meta["line_count"] == 0

    def test_tail_mode(self, log_file):
        result = builtin_log_read_lines(path=str(log_file), tail_lines=5)
        assert result.ok is True
        assert len(result.content) == 5
        assert "line-9999" in result.content[-1]
        assert result.meta["mode"] == "tail"

    def test_tail_no_phantom_empty_line(self, tmp_path):
        """文件以换行结尾时不应多出空行；不以换行结尾时最后一行不丢。"""
        with_nl = tmp_path / "nl.log"
        with_nl.write_text("a\nb\nc\n", encoding="utf-8")
        assert builtin_log_read_lines(path=str(with_nl), tail_lines=10).content == ["a", "b", "c"]
        no_nl = tmp_path / "nonl.log"
        no_nl.write_text("a\nb\nc", encoding="utf-8")
        assert builtin_log_read_lines(path=str(no_nl), tail_lines=10).content == ["a", "b", "c"]

    def test_tail_larger_than_file(self, tmp_path):
        small = tmp_path / "t.log"
        small.write_text("x\ny\n", encoding="utf-8")
        result = builtin_log_read_lines(path=str(small), tail_lines=100)
        assert result.content == ["x", "y"]

    def test_long_line_truncated(self, tmp_path):
        big_line = tmp_path / "long.log"
        big_line.write_text("A" * 10_000 + "\nok\n", encoding="utf-8")
        result = builtin_log_read_lines(path=str(big_line), max_lines=2)
        assert result.content[0].endswith("…[truncated]")
        assert len(result.content[0]) <= 4100
        assert result.content[1] == "ok"

    def test_not_found(self, tmp_path):
        result = builtin_log_read_lines(path=str(tmp_path / "nope.log"))
        assert result.ok is False

    def test_directory_rejected(self, tmp_path):
        result = builtin_log_read_lines(path=str(tmp_path))
        assert result.ok is False
        assert result.error == "not_a_file"


# ---- log_search -------------------------------------------------------------------


class TestLogSearch:
    def test_literal_search(self, log_file):
        result = builtin_log_search(path=str(log_file), pattern="ERROR boom-")
        assert result.ok is True
        assert result.meta["hit_count"] == 10
        first = result.content[0]
        assert first["line_no"] == 500
        assert "boom-500" in first["line"]

    def test_regex_case_insensitive(self, log_file):
        result = builtin_log_search(
            path=str(log_file), pattern="error BOOM-\\d+", is_regex=True, case_insensitive=True
        )
        assert result.ok is True
        assert result.meta["hit_count"] == 10

    def test_context_lines(self, log_file):
        result = builtin_log_search(
            path=str(log_file), pattern="boom-500", context_lines=2, max_results=1
        )
        hit = result.content[0]
        assert len(hit["before"]) == 2
        assert all("line-" in b for b in hit["before"])
        assert len(hit["after"]) == 2
        assert "Foo.java" in hit["after"][0]
        assert "Caused by" in hit["after"][1]

    def test_max_results_truncate_and_early_stop(self, log_file):
        result = builtin_log_search(path=str(log_file), pattern="INFO line-", max_results=5)
        assert result.ok is True
        assert result.meta["hit_count"] == 5
        assert result.meta["truncated"] is True
        # 早停：远未扫完全部 1 万行
        assert result.meta["scanned_lines"] < _TOTAL_LINES // 2

    def test_full_scan_counts_all_lines(self, log_file):
        result = builtin_log_search(path=str(log_file), pattern="no_such_token_xyz")
        assert result.ok is True
        assert result.meta["hit_count"] == 0
        assert result.meta["scanned_lines"] == _TOTAL_LINES + 20  # 10 个 ERROR 块各 +2 堆栈行

    def test_empty_pattern(self, log_file):
        result = builtin_log_search(path=str(log_file), pattern="")
        assert result.ok is False
        assert result.error == "empty_pattern"

    def test_pattern_too_long(self, log_file):
        result = builtin_log_search(path=str(log_file), pattern="x" * 2000)
        assert result.ok is False
        assert result.error == "pattern_too_long"

    def test_invalid_regex(self, log_file):
        result = builtin_log_search(path=str(log_file), pattern="([", is_regex=True)
        assert result.ok is False
        assert result.error == "invalid_regex"

    def test_not_found(self, tmp_path):
        result = builtin_log_search(path=str(tmp_path / "nope.log"), pattern="x")
        assert result.ok is False


# ---- 只读保证 + 登记 + dispatcher ---------------------------------------------------


class TestReadOnlyAndRegistration:
    def test_tools_never_modify_file(self, log_file):
        before = _sha256(log_file)
        builtin_log_read_lines(path=str(log_file), tail_lines=10)
        builtin_log_search(path=str(log_file), pattern="ERROR")
        assert _sha256(log_file) == before

    def test_names_registered(self):
        for name in ("log_read_lines", "log_search"):
            assert name in BUILTIN_TOOL_NAMES

    def test_schema_description_risk(self):
        for name in ("log_read_lines", "log_search"):
            assert get_builtin_schema(name) is not None
            assert TOOL_DESCRIPTIONS.get(name)
            assert TOOL_RISK_LEVEL[name] == "read"

    def test_registry_callable(self):
        reg = get_default_registry()
        for name in ("log_read_lines", "log_search"):
            assert reg.has(name)
            assert callable(reg.get(name))

    async def test_dispatcher_read_direct(self, log_file):
        """read 风险不经 HITL 直接执行。"""
        from agent.builtin.dispatcher import dispatcher
        from agent.builtin.registry import reset_default_registry

        reset_default_registry()
        result = await dispatcher().dispatch(
            {
                "server": "builtin",
                "name": "log_search",
                "args": {"path": str(log_file), "pattern": "boom-500"},
            },
            {"run_id": "test-v7"},
        )
        assert result is not None
        assert result["tool_result"]["ok"] is True
        assert result["tool_result"]["meta"]["hit_count"] == 1
