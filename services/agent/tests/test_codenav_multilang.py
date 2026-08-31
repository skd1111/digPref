"""test_codenav_multilang.py —— 多语言符号抽取回归（2026-08-28 扩展）。

背景：此前仅支持 java/py/ts/tsx（后补 js/jsx），导入纯 JS 前端工程提取 0
功能点（#173）。本次补齐常见语言：Go / C / C++ / C# / PHP / Ruby / Rust /
Kotlin / Swift / Scala，以及 Vue SFC（<script> 块复用 JS/TS 解析）。
节点类型均来自实测 AST，新增语言请同步更新 _probe 记录。
"""

from __future__ import annotations

from pathlib import Path

from agent.codenav.indexer import WorkspaceIndexer
from agent.codenav.language_registry import get_supported_extensions


def _extract(tmp_path: Path, name: str, content: str, language: str | None = None):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    syms = idx.extract_symbols(str(f), language)
    return {(s.kind, s.name): s for s in syms}


def test_registry_covers_common_languages():
    exts = set(get_supported_extensions())
    for e in (
        ".java",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".c",
        ".h",
        ".cpp",
        ".cs",
        ".php",
        ".rb",
        ".rs",
        ".kt",
        ".swift",
        ".scala",
        ".vue",
    ):
        assert e in exts, f"{e} 未注册"


def test_go(tmp_path):
    by = _extract(
        tmp_path,
        "m.go",
        """
package main
func Add(a, b int) int { return a + b }
type User struct { Name string }
func (u *User) Rename(n string) { u.Name = n }
""",
    )
    assert ("function", "Add") in by
    assert ("method", "Rename") in by
    assert ("class", "User") in by


def test_c(tmp_path):
    by = _extract(
        tmp_path,
        "m.c",
        """
struct Point { int x; };
int add(int a, int b) { return a + b; }
""",
    )
    assert ("class", "Point") in by
    assert ("function", "add") in by


def test_cpp(tmp_path):
    by = _extract(
        tmp_path,
        "m.cpp",
        """
class Shape { public: double area() { return 0; } };
int add(int a, int b) { return a + b; }
""",
    )
    assert ("class", "Shape") in by
    assert ("function", "add") in by
    # 类体内联定义 → method
    assert by[("method", "area")].parent_class == "Shape"


def test_csharp(tmp_path):
    by = _extract(
        tmp_path,
        "M.cs",
        """
namespace Demo {
  public class OrderService { public decimal Calc() { return 0; } }
  public interface IRepo { void Save(); }
}
""",
    )
    assert ("class", "OrderService") in by
    assert ("interface", "IRepo") in by
    assert by[("method", "Calc")].parent_class == "OrderService"


def test_php(tmp_path):
    by = _extract(
        tmp_path,
        "m.php",
        """<?php
class UserService { public function find($id) { return null; } }
function helper() { return 1; }
""",
    )
    assert ("class", "UserService") in by
    assert ("function", "helper") in by
    assert by[("method", "find")].parent_class == "UserService"


def test_ruby(tmp_path):
    by = _extract(
        tmp_path,
        "m.rb",
        """
class UserService
  def find(id)
    nil
  end
end
def helper; 1; end
""",
    )
    assert ("class", "UserService") in by
    assert ("function", "helper") in by
    assert by[("method", "find")].parent_class == "UserService"


def test_rust(tmp_path):
    by = _extract(
        tmp_path,
        "m.rs",
        """
pub struct Order { total: u64 }
impl Order { pub fn calc(&self) -> u64 { self.total } }
fn helper() -> u32 { 1 }
""",
    )
    assert ("class", "Order") in by
    assert ("function", "helper") in by
    assert by[("method", "calc")].parent_class == "Order"


def test_kotlin(tmp_path):
    by = _extract(
        tmp_path,
        "M.kt",
        """
class UserService { fun find(): Int { return 1 } }
fun helper(): Int { return 1 }
""",
    )
    assert ("class", "UserService") in by
    assert ("function", "helper") in by
    assert by[("method", "find")].parent_class == "UserService"


def test_swift(tmp_path):
    by = _extract(
        tmp_path,
        "M.swift",
        """
class UserService { func find() -> Int { return 1 } }
func helper() -> Int { return 1 }
""",
    )
    assert ("class", "UserService") in by
    assert ("function", "helper") in by
    assert by[("method", "find")].parent_class == "UserService"


def test_scala(tmp_path):
    by = _extract(
        tmp_path,
        "M.scala",
        """
class UserService { def find(): String = "" }
object App { def main(args: Array[String]): Unit = () }
""",
    )
    assert ("class", "UserService") in by
    assert ("class", "App") in by
    assert by[("method", "find")].parent_class == "UserService"


def test_vue_script_js_and_ts(tmp_path):
    vue = """<template>
  <div>{{ msg }}</div>
</template>

<script lang="ts">
export class OrderLogic {
  calc(o: { total: number }): number { return o.total; }
}
</script>

<script>
export function formatPrice(n) { return n.toFixed(2); }
</script>
"""
    f = tmp_path / "Order.vue"
    f.write_text(vue, encoding="utf-8")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    syms = idx.extract_symbols(str(f))
    by = {(s.kind, s.name): s for s in syms}
    assert ("class", "OrderLogic") in by
    assert ("method", "calc") in by
    assert ("function", "formatPrice") in by
    # language 统一标 vue；行号按块在文件中的位置偏移
    assert all(s.language == "vue" for s in syms)
    assert by[("class", "OrderLogic")].start_line == 6
    assert by[("function", "formatPrice")].start_line == 12


def test_vue_empty_script_skipped(tmp_path):
    f = tmp_path / "Empty.vue"
    f.write_text("<template><div/></template>\n<script></script>\n", encoding="utf-8")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    assert idx.extract_symbols(str(f)) == []


async def test_full_scan_picks_up_new_languages(tmp_path):
    """_iter_files 白名单含新后缀 → 全量扫描能写库。"""
    (tmp_path / "a.go").write_text("package m\nfunc F() {}\n", encoding="utf-8")
    (tmp_path / "b.vue").write_text("<script>export function g() {}</script>", encoding="utf-8")
    idx = WorkspaceIndexer(db_path=str(tmp_path / "idx.db"), root_paths=[tmp_path])
    await idx.full_scan()
    status = idx.get_status()
    assert status.total_files >= 2
    assert status.total_symbols >= 2
