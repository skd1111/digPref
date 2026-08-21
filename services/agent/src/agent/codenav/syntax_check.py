"""语法错误检查 —— tree-sitter 解析找 ERROR / MISSING 节点（2026-08-19）。

定位：纯语法级校验（括号/引号不闭合、缺分号、结构错乱），不做语义分析
（类型不匹配、符号未定义那类需要编译器级能力，不在范围内）。

复用 language_registry 的 parser（与索引器同源），解析后收集最外层错误节点：
    - node.type == "ERROR"  → 该区间内容无法归入任何语法规则
    - node.is_missing       → 解析器在此处期望某个 token 但缺失（如缺分号）

红线：错误节点不递归内部（避免一个错误炸出几十条重复诊断）；
诊断数量有上限，防极端文件把响应撑爆。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tree_sitter import Node

from agent.codenav.language_registry import get_parser_for_file

# 单次检查最多返回的诊断数（防极端文件刷屏）
_MAX_DIAGNOSTICS = 100


@dataclass
class SyntaxDiagnostic:
    """单条语法诊断；行列均为 1-based 含端点（与 Monaco marker 口径一致）。"""

    line: int
    column: int
    end_line: int
    end_column: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "message": self.message,
        }


def check_syntax(file_path: str, content: str) -> tuple[str, list[SyntaxDiagnostic]]:
    """按 file_path 后缀选语法解析 content，返回 (language_id, 诊断列表)。

    不支持的后缀返回 ("", []) —— 调用方据此跳过校验（不算错误）。
    """
    got = get_parser_for_file(file_path)
    if got is None:
        return "", []
    parser, lang_id = got
    tree = parser.parse(content.encode("utf-8"))
    diagnostics: list[SyntaxDiagnostic] = []
    _collect_errors(tree.root_node, diagnostics)
    return lang_id, diagnostics


def _collect_errors(node: Node, out: list[SyntaxDiagnostic]) -> None:
    """深度优先收集最外层错误节点（达上限即早停）。"""
    if len(out) >= _MAX_DIAGNOSTICS:
        return
    if node.type == "ERROR" or node.is_missing:
        out.append(_to_diagnostic(node))
        return  # 不再深入错误节点内部
    for child in node.children:
        if len(out) >= _MAX_DIAGNOSTICS:
            return
        _collect_errors(child, out)


def _to_diagnostic(node: Node) -> SyntaxDiagnostic:
    # tree-sitter 行列 0-based → 转 1-based；missing 节点零宽度，终点列 +1
    # 保证 Monaco 能画出波浪线
    start = node.start_point
    end = node.end_point
    if node.is_missing:
        message = f'语法错误：此处缺少 "{node.type}"'
        end_col = end.column + 1
    else:
        raw = node.text or b""
        snippet = raw.decode("utf-8", errors="replace").strip().splitlines()
        first = snippet[0][:32] if snippet else ""
        message = f'语法错误：无法解析 "{first}"' if first else "语法错误：无法解析该片段"
        end_col = end.column if end.column > start.column else start.column + 1
    return SyntaxDiagnostic(
        line=start.row + 1,
        column=start.column + 1,
        end_line=end.row + 1 if not node.is_missing else start.row + 1,
        end_column=end_col,
        message=message,
    )
