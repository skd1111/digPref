"""V4 扩展工具测试（2026-08-04）。

覆盖：
    - mkdir Python 兜底（独立运行场景）
    - http_post（参数校验 + 成功路径）
    - git_status / git_diff / git_log / git_commit（真实 git 仓库）
    - symbol_search / file_symbols / biznav_features（索引缺失降级）
    - MCP 工具摘要关键词自动提取
    - 登记完整性：names / schema / 描述 / 风险 / registry callable
"""

from __future__ import annotations

import shutil
import subprocess

import httpx
import pytest
from agent.builtin.capabilities import (
    builtin_biznav_features,
    builtin_file_symbols,
    builtin_symbol_search,
)
from agent.builtin.extra import builtin_http_post
from agent.builtin.fallbacks import builtin_mkdir_py
from agent.builtin.git import (
    builtin_git_commit,
    builtin_git_diff,
    builtin_git_log,
    builtin_git_status,
)
from agent.builtin.models import BUILTIN_TOOL_NAMES, is_rust_tool
from agent.builtin.registry import (
    TOOL_DESCRIPTIONS,
    TOOL_RISK_LEVEL,
    get_default_registry,
)
from agent.builtin.schemas import get_builtin_schema
from agent.tools.catalog import _extract_mcp_keywords

_HAS_GIT = shutil.which("git") is not None


# ---- mkdir 兜底 --------------------------------------------------------------


class TestMkdirFallback:
    def test_create_simple(self, tmp_path):
        target = tmp_path / "newdir"
        result = builtin_mkdir_py(path=str(target))
        assert result.ok is True
        assert target.is_dir()
        assert result.content["created"] is True

    def test_create_parents(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        result = builtin_mkdir_py(path=str(target), parents=True)
        assert result.ok is True
        assert target.is_dir()

    def test_parents_required_but_missing(self, tmp_path):
        target = tmp_path / "x" / "y"
        result = builtin_mkdir_py(path=str(target), parents=False)
        assert result.ok is False

    def test_existing_dir_is_ok(self, tmp_path):
        result = builtin_mkdir_py(path=str(tmp_path))
        assert result.ok is True
        assert result.content["created"] is False

    def test_existing_file_conflict(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x", encoding="utf-8")
        result = builtin_mkdir_py(path=str(f))
        assert result.ok is False
        assert result.error == "exists_not_dir"


# ---- http_post ---------------------------------------------------------------


class TestHttpPost:
    async def test_invalid_url(self):
        result = await builtin_http_post(url="ftp://example.com", json_body={"a": 1})
        assert result.ok is False
        assert result.error == "invalid_url"

    async def test_missing_body(self):
        result = await builtin_http_post(url="https://example.com/api")
        assert result.ok is False
        assert result.error == "missing_body"

    async def test_success_json_body(self, monkeypatch):
        class _FakeResponse:
            status_code = 201
            content = b'{"id": 7}'
            headers = {"content-type": "application/json"}  # noqa: RUF012 测试 fake 常量

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json=None, data=None, headers=None):
                assert json == {"name": "test"}
                return _FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        result = await builtin_http_post(url="https://example.com/api", json_body={"name": "test"})
        assert result.ok is True
        assert result.content["status_code"] == 201
        assert result.risk_level == "medium"


# ---- git 工具族 ---------------------------------------------------------------


@pytest.mark.skipif(not _HAS_GIT, reason="git not installed")
class TestGitTools:
    @pytest.fixture
    def repo(self, tmp_path):
        """初始化一个可用的 git 仓库（本地 user 配置 + 首次提交）。"""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Tester"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        (tmp_path / "README.md").write_text("hello", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        return tmp_path

    async def test_status_clean_then_dirty(self, repo):
        clean = await builtin_git_status(repo=str(repo))
        assert clean.ok is True
        assert "## " in clean.content["stdout"]
        (repo / "README.md").write_text("changed", encoding="utf-8")
        dirty = await builtin_git_status(repo=str(repo))
        assert dirty.ok is True
        assert "README.md" in dirty.content["stdout"]

    async def test_diff_shows_change(self, repo):
        (repo / "README.md").write_text("changed", encoding="utf-8")
        result = await builtin_git_diff(repo=str(repo))
        assert result.ok is True
        assert "+changed" in result.content["stdout"]

    async def test_log(self, repo):
        result = await builtin_git_log(repo=str(repo), limit=5)
        assert result.ok is True
        assert "init" in result.content["stdout"]

    async def test_commit_empty_message_rejected(self, repo):
        result = await builtin_git_commit(repo=str(repo), message="  ")
        assert result.ok is False
        assert result.error == "empty_message"

    async def test_commit_staged_changes(self, repo):
        (repo / "new.txt").write_text("data", encoding="utf-8")
        subprocess.run(["git", "add", "new.txt"], cwd=repo, capture_output=True, check=True)
        result = await builtin_git_commit(repo=str(repo), message="feat: add new.txt")
        assert result.ok is True
        log = await builtin_git_log(repo=str(repo), limit=2)
        assert "feat: add new.txt" in log.content["stdout"]

    async def test_commit_nothing_staged_fails(self, repo):
        result = await builtin_git_commit(repo=str(repo), message="noop")
        assert result.ok is False

    async def test_not_a_repo(self, tmp_path):
        result = await builtin_git_status(repo=str(tmp_path))
        assert result.ok is False


# ---- 内部能力只读入口 ----------------------------------------------------------


class TestCapabilityTools:
    def test_symbol_search_index_missing(self, monkeypatch):
        monkeypatch.setenv("EAIDE_WORKSPACE_INDEX_DB", "/nonexistent/idx/workspace.db")
        result = builtin_symbol_search(name="foo")
        assert result.ok is False
        assert result.error == "index_not_built"

    def test_file_symbols_index_missing(self, monkeypatch):
        monkeypatch.setenv("EAIDE_WORKSPACE_INDEX_DB", "/nonexistent/idx/workspace.db")
        result = builtin_file_symbols(file_path="/tmp/a.py")
        assert result.ok is False
        assert result.error == "index_not_built"

    def test_biznav_features_db_missing(self, monkeypatch):
        monkeypatch.setenv("EAIDE_BIZNAV_DB", "/nonexistent/idx/biznav.db")
        result = builtin_biznav_features(project_name="demo")
        assert result.ok is False
        assert result.error == "biznav_not_built"

    def test_symbol_search_with_index(self, tmp_path, monkeypatch):
        """真实索引库：建库 + 插入符号后可检索。"""
        from agent.codenav.indexer import _read_schema_sql

        db = tmp_path / "workspace_index.db"
        import sqlite3

        with sqlite3.connect(db) as conn:
            conn.executescript(_read_schema_sql())
            conn.execute(
                "INSERT INTO symbols (name, kind, file_path, start_line, end_line, "
                "signature, parent_class, language, last_modified) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("foo_bar", "function", "/x.py", 1, 5, "def foo_bar()", None, "python", 0),
            )
            conn.commit()
        monkeypatch.setenv("EAIDE_WORKSPACE_INDEX_DB", str(db))
        result = builtin_symbol_search(name="foo")
        assert result.ok is True
        assert result.content[0]["name"] == "foo_bar"


# ---- MCP 关键词提取 ------------------------------------------------------------


class TestMcpKeywordExtraction:
    def test_name_parts_kept(self):
        kw = _extract_mcp_keywords("database.query", "Execute a SQL SELECT query")
        assert "database" in kw
        assert "query" in kw

    def test_chinese_description(self):
        kw = _extract_mcp_keywords("rest.request", "调用订单系统的接口查询数据")
        assert any("订单" in w for w in kw)

    def test_stopwords_and_limit(self):
        kw = _extract_mcp_keywords(
            "s.t",
            "the and use tool 使用 调用 " + "word " * 30,
        )
        assert "the" not in kw and "use" not in kw and "使用" not in kw
        assert len(kw) <= 8


# ---- 登记完整性 ----------------------------------------------------------------


class TestRegistrationConsistency:
    def test_every_tool_has_schema_description_risk(self):
        for name in BUILTIN_TOOL_NAMES:
            assert get_builtin_schema(name) is not None, f"{name} missing schema"
            assert TOOL_DESCRIPTIONS.get(name), f"{name} missing description"
            assert name in TOOL_RISK_LEVEL, f"{name} missing risk level"

    def test_registry_has_all_python_tools(self):
        reg = get_default_registry()
        for name in BUILTIN_TOOL_NAMES:
            if is_rust_tool(name):
                continue  # Rust 工具走 tauri bridge，不在 registry
            assert reg.has(name), f"{name} not registered"
            assert callable(reg.get(name))

    def test_write_tools_are_medium_or_above(self):
        for name in ("http_post", "git_commit", "mkdir"):
            assert TOOL_RISK_LEVEL[name] in ("medium", "high", "critical"), name
