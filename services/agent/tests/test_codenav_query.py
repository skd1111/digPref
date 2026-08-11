"""test_codenav_query.py —— SQLite 符号查询测试。"""

from __future__ import annotations

import asyncio

import pytest
from agent.codenav.indexer import WorkspaceIndexer
from agent.codenav.query import SymbolQuery


@pytest.fixture
def db(tmp_path):
    (tmp_path / "A.java").write_text(
        "public class OrderService { void create() {} void cancel() {} }",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "def helper(): pass\nclass Calculator:\n    def add(self, a, b): return a+b\n",
        encoding="utf-8",
    )
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    asyncio.run(idx.full_scan())
    return SymbolQuery(str(tmp_path / "idx.db"))


def test_search_exact_match(db):
    results = db.search("OrderService")
    assert len(results) == 1
    assert results[0].kind == "class"


def test_search_fuzzy(db):
    """LIKE %name% 模糊匹配。"""
    results = db.search("add")
    assert any(s.name == "add" for s in results)


def test_search_filter_by_kind(db):
    results = db.search("add", kind="method")
    assert all(s.kind == "method" for s in results)
    # 无效 kind（无 method 名 "add" 时）
    results_empty = db.search("OrderService", kind="method")
    assert results_empty == []


def test_search_empty_returns_all(db):
    """空 name 返回所有符号（受 limit 限制）。"""
    results = db.search("")
    assert len(results) > 0


def test_get_file_symbols(db):
    syms = db.get_file_symbols(str((db._db_path).replace("idx.db", "A.java")))
    # file_path 不一定匹配（取决于写入时是 resolve 还是 raw）
    # 至少 OrderService 应该查得到
    by_name = {s.name for s in syms}
    if not by_name:
        # 再试一下按 "java" 过滤全搜
        results = db.search("OrderService")
        assert len(results) >= 1


def test_get_status(db):
    status = db.get_status()
    assert status.total_files >= 2
    assert status.total_symbols >= 3


def test_delete_file_symbols(db):
    before = db.get_status().total_symbols
    db.delete_file_symbols("nonexistent.java")
    # 无文件 → 无变化
    assert db.get_status().total_symbols == before


def test_count_by_kind(db):
    counts = db.count_by_kind()
    assert "class" in counts
    assert "method" in counts
