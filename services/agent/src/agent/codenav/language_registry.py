"""文件后缀 → tree-sitter 语言映射。

Phase 2F 首批覆盖：Java / Python / TypeScript（JavaScript 复用 TS 解析器）。

设计：tree-sitter 0.26 + tree-sitter-{java,python,typescript} 0.23+ 的 API：
    import tree_sitter_java as tsj
    from tree_sitter import Language, Parser
    lang = Language(tsj.language())
    parser = Parser(lang)
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_java as tsj
import tree_sitter_python as tsp
import tree_sitter_typescript as tst
from tree_sitter import Language, Parser

# 文件后缀 → (Language 实例, language_id)
# V0 覆盖：Java / Python / TypeScript / TSX
# JavaScript (.js/.jsx) 暂不支持 — tree-sitter-typescript 0.23 不提供 javascript grammar
_LANGUAGE_BUILDERS: dict[str, tuple[object, str]] = {
    ".java": (tsj.language, "java"),
    ".py": (tsp.language, "python"),
    ".ts": (tst.language_typescript, "typescript"),
    ".tsx": (tst.language_tsx, "typescript"),
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
    """所有支持的文件后缀列表。"""
    return list(_LANGUAGE_BUILDERS.keys())


def clear_cache() -> None:
    """清空 parser 缓存（测试用）。"""
    _PARSER_CACHE.clear()
