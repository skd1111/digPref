"""文件后缀 → tree-sitter 语言映射。

首批覆盖：Java / Python / TypeScript；
2026-08-28 扩展：JavaScript、Go、C、C++、C#、PHP、Ruby、Rust、
Kotlin、Swift、Scala（常见后端/移动端语言），及 .vue 虚拟后缀。

设计：tree-sitter 0.26 + tree-sitter-* 绑定包的 API：
    import tree_sitter_java as tsj
    from tree_sitter import Language, Parser
    lang = Language(tsj.language())
    parser = Parser(lang)
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_c
import tree_sitter_c_sharp
import tree_sitter_cpp
import tree_sitter_go
import tree_sitter_java as tsj
import tree_sitter_javascript as tsjs
import tree_sitter_kotlin
import tree_sitter_php
import tree_sitter_python as tsp
import tree_sitter_ruby
import tree_sitter_rust
import tree_sitter_scala
import tree_sitter_swift
import tree_sitter_typescript as tst
from tree_sitter import Language, Parser

# 文件后缀 → (Language 构造器, language_id)
_LANGUAGE_BUILDERS: dict[str, tuple[object, str]] = {
    ".java": (tsj.language, "java"),
    ".py": (tsp.language, "python"),
    ".ts": (tst.language_typescript, "typescript"),
    ".tsx": (tst.language_tsx, "typescript"),
    ".js": (tsjs.language, "javascript"),
    ".jsx": (tsjs.language, "javascript"),
    ".go": (tree_sitter_go.language, "go"),
    ".c": (tree_sitter_c.language, "c"),
    ".h": (tree_sitter_c.language, "c"),
    ".cpp": (tree_sitter_cpp.language, "cpp"),
    ".cc": (tree_sitter_cpp.language, "cpp"),
    ".hpp": (tree_sitter_cpp.language, "cpp"),
    ".cs": (tree_sitter_c_sharp.language, "csharp"),
    ".php": (tree_sitter_php.language_php, "php"),
    ".rb": (tree_sitter_ruby.language, "ruby"),
    ".rs": (tree_sitter_rust.language, "rust"),
    ".kt": (tree_sitter_kotlin.language, "kotlin"),
    ".swift": (tree_sitter_swift.language, "swift"),
    ".scala": (tree_sitter_scala.language, "scala"),
}

# 虚拟后缀：PyPI 无独立 grammar 包，不进 _LANGUAGE_BUILDERS，但参与扫描/索引白名单。
# 由使用方自行特殊处理：.vue 在 indexer 里抽 <script> 块后复用 JS/TS 解析；
# get_parser_for_file 对其返回 None → 语法检查跳过（不算错误）。
_VIRTUAL_EXTENSIONS: dict[str, str] = {
    ".vue": "vue",
}

_PARSER_CACHE: dict[str, Parser] = {}


def _build_parser(ext: str) -> Parser | None:
    """构造并缓存 Parser。"""
    if ext in _PARSER_CACHE:
        return _PARSER_CACHE[ext]
    builder = _LANGUAGE_BUILDERS.get(ext)
    if not builder:
        return None
    lang_fn, _ = builder
    lang = Language(lang_fn())
    parser = Parser(lang)
    _PARSER_CACHE[ext] = parser
    return parser


def get_parser_for_file(file_path: str) -> tuple[Parser, str] | None:
    """根据文件后缀返回 (Parser, language_id)；不支持返回 None。

    例：get_parser_for_file("foo.java") → (Parser, "java")
    """
    ext = Path(file_path).suffix.lower()
    parser = _build_parser(ext)
    if not parser:
        return None
    _, lang_id = _LANGUAGE_BUILDERS[ext]
    return parser, lang_id


def get_supported_extensions() -> list[str]:
    """所有受支持的文件后缀（含虚拟后缀；扫描/索引白名单的唯一来源）。"""
    return [*list(_LANGUAGE_BUILDERS.keys()), *list(_VIRTUAL_EXTENSIONS.keys())]


def get_virtual_language_id(file_path: str) -> str | None:
    """虚拟后缀的 language_id（无 grammar，调用方自行处理）；非虚拟返回 None。"""
    return _VIRTUAL_EXTENSIONS.get(Path(file_path).suffix.lower())


def clear_cache() -> None:
    """清空 parser 缓存（测试用）。"""
    _PARSER_CACHE.clear()
