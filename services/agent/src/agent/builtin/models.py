"""Phase 1B · 原生工具层 —— 数据模型。

ToolResult 是统一返回类型，跨 builtin / MCP 工具共用。
RiskLevel 复用 safety/policy.py 5 级分类。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# 风险等级 —— 复用 safety/policy.py 的 5 级分类
# (read / low / medium / high / critical)
RiskLevel = Literal["read", "low", "medium", "high", "critical"]


# V0 + V1 工具名清单（5 核心 + 5 轻量 = 10 Python；V1 接力 9 Rust 占位 = 19 总）
# V1 轻量工具（无 I/O / 无 LLM）：calculator / json_parse / json_format / regex_match / url_parse
# V1 Rust 工具（V1.5 接力 Rust 端 9 工具真实实现，本文件仅声明名字便于 type hint）
BUILTIN_TOOL_NAMES: tuple[str, ...] = (
    # V0 Python 5 核心
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "grep",
    # V1 Python 5 轻量
    "calculator",
    "json_parse",
    "json_format",
    "regex_match",
    "url_parse",
    # V1 Rust 9 工具占位（V1.5 接力真实实现）
    "stat_file",
    "mkdir",
    "delete_file",
    "move_file",
    "find",
    "glob",
    "hash",
    "base64",
    "shell",
    # V3 Python 常用工具（2026-08-03）
    "datetime_now",
    "date_parse",
    "uuid4",
    "http_get",
    "csv_parse",
    "text_split",
    # V4 扩展工具（2026-08-04：CODE/WORK 模式能力补齐）
    "http_post",
    "git_status",
    "git_diff",
    "git_log",
    "git_commit",
    # V4 内部能力只读入口（codenav / biznav）
    "symbol_search",
    "file_symbols",
    "biznav_features",
    # V5 文件转换工具（2026-08-10：markitdown 文件转 Markdown）
    "file_to_markdown",
    # V6 文档处理工具族（2026-08-10：Excel / PDF / Word 结构化读写）
    "excel_query",
    "excel_export",
    "pdf_merge",
    "pdf_split",
    "word_generate",
    # V7 大文件查看与搜索（2026-08-10：klogg 式只读，突破 100MB 限制）
    "log_read_lines",
    "log_search",
)


# V1 Rust 工具名清单（dispatcher 据此分流到 Tauri Command 远端调用）
RUST_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "stat_file",
        "mkdir",
        "delete_file",
        "move_file",
        "find",
        "glob",
        "hash",
        "base64",
        "shell",
    }
)


def is_rust_tool(name: str) -> bool:
    """判断工具名是否属于 Rust 端（V1 占位：V1.5 前 dispatcher 返 not_implemented）。

    Args:
        name: 工具名（不带 builtin_ 前缀）。

    Returns:
        True if name in RUST_TOOL_NAMES.
    """
    return name in RUST_TOOL_NAMES


# ---- 异常 ----------------------------------------------------------------------


class PathSecurityError(ValueError):
    """路径命中安全黑名单（Windows 保留名 / UNC / null byte）。"""


class PathOutOfBoundsError(PermissionError):
    """路径不在 allowed_roots 白名单内。"""


# ---- ToolResult ---------------------------------------------------------------


@dataclass
class ToolResult:
    """内置工具统一返回类型。

    Attributes:
        ok: 是否成功。
        content: 主体内容（read_file 时是 str, list_dir 时是 list[dict]）。
        error: 错误代码（path_out_of_bounds / file_too_large / not_found / ...）。
        hint: 建议（use logviewer / 需要 HITL / 文件过大）。
        meta: 元数据（size / line_count / hit_count / elapsed_ms）。
        needs_hitl: 是否触发 HITL（dispatcher 写操作时设置）。
        risk_level: 风险等级（供 audit / downstream policy）。
    """

    ok: bool = False
    content: Any = None
    error: str | None = None
    hint: str | None = None
    meta: dict = field(default_factory=dict)
    needs_hitl: bool = False
    risk_level: RiskLevel = "read"

    def to_dict(self) -> dict:
        """序列化为 dict（存入 AgentState / audit / SSE）。"""
        return {
            "ok": self.ok,
            "content": self.content,
            "error": self.error,
            "hint": self.hint,
            "meta": self.meta,
            "needs_hitl": self.needs_hitl,
            "risk_level": self.risk_level,
        }

    @classmethod
    def from_exception(cls, exc: Exception, *, risk_level: RiskLevel = "read") -> ToolResult:
        """从异常构造失败结果。"""
        return cls(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            risk_level=risk_level,
        )


# ---- BuiltinTool Protocol -----------------------------------------------------


@runtime_checkable
class BuiltinTool(Protocol):
    """所有内置工具的统一接口（V0 用于 type hint / 测试，V1 真实继承）。

    V0 5 工具是模块级函数而不是类，Protocol 主要给 dispatcher 调用时
    提示返回签名。
    """

    name: str

    async def __call__(self, args: dict) -> ToolResult: ...
