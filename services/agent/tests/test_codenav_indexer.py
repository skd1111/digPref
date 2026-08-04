"""test_codenav_indexer.py —— Phase 2F AST 提取准确性测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.codenav.indexer import WorkspaceIndexer


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_extract_java_class_and_methods(tmp_path):
    f = tmp_path / "InterestService.java"
    _write(f, """
public class InterestService {
    public void calculateInterest(java.math.BigDecimal amt) { }
    private int getRate() { return 0; }
}
""")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    syms = idx.extract_symbols(str(f))
    by_name = {(s.kind, s.name): s for s in syms}
    assert ("class", "InterestService") in by_name
    cls = by_name[("class", "InterestService")]
    assert cls.start_line == 2
    assert ("method", "calculateInterest") in by_name
    m1 = by_name[("method", "calculateInterest")]
    assert m1.parent_class == "InterestService"
    assert "calculateInterest" in (m1.signature or "")
    assert ("method", "getRate") in by_name


def test_extract_python_function_and_class(tmp_path):
    f = tmp_path / "mod.py"
    _write(f, """
class Calculator:
    def add(self, a, b):
        return a + b

def helper(x):
    return x * 2
""")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    syms = idx.extract_symbols(str(f))
    by_name = {(s.kind, s.name): s for s in syms}
    assert ("class", "Calculator") in by_name
    assert ("method", "add") in by_name
    assert by_name[("method", "add")].parent_class == "Calculator"
    assert ("function", "helper") in by_name


def test_extract_typescript_exported_class(tmp_path):
    f = tmp_path / "app.ts"
    _write(f, """
export class UserService {
    async findUser(id: string) { return null; }
}

export function util(x: number) { return x; }

export interface IRepo { save(x: any): void; }
""")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    syms = idx.extract_symbols(str(f))
    by_name = {(s.kind, s.name): s for s in syms}
    assert ("class", "UserService") in by_name
    assert ("method", "findUser") in by_name
    assert by_name[("method", "findUser")].parent_class == "UserService"
    assert ("function", "util") in by_name
    assert ("interface", "IRepo") in by_name


def test_unsupported_extension_skipped(tmp_path):
    f = tmp_path / "readme.txt"
    _write(f, "not code")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    assert idx.extract_symbols(str(f)) == []


def test_java_interface_and_enum(tmp_path):
    f = tmp_path / "Decls.java"
    _write(f, """
interface Animal { void speak(); }
enum Color { RED, GREEN, BLUE }
""")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    syms = idx.extract_symbols(str(f))
    names = {s.name for s in syms}
    assert "Animal" in names
    assert "Color" in names
    kinds = {s.name: s.kind for s in syms}
    assert kinds["Animal"] == "interface"
    assert kinds["Color"] == "enum"


def test_full_scan_writes_sqlite(tmp_path):
    (tmp_path / "A.java").write_text("public class A { void m() {} }", encoding="utf-8")
    (tmp_path / "b.py").write_text("def f(): pass", encoding="utf-8")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    import asyncio
    status = asyncio.run(idx.full_scan())
    assert status.total_files == 2
    assert status.total_symbols >= 3  # A class + m method + f function
    assert status.last_full_scan is not None
