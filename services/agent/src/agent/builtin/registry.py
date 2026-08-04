"""Phase 1B · BuiltinToolRegistry —— 工具注册表 + 描述生成。

V0 工具是模块级函数（不是类），所以 registry 主要做：
  1. name → callable 映射
  2. generate_tool_descriptions() 生成注入 LLM 的 system prompt 片段
  3. 风险等级映射（用于 audit / HITL 决策）

V1 扩展：
  - 真实继承 BuiltinTool Protocol
  - Rust 工具注册（通过 Tauri Command 远端调用）
  - 5 Python 轻量工具（calculator / json_parse / json_format / regex_match / url_parse）
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from agent.builtin.files import (
    builtin_edit_file,
    builtin_list_dir,
    builtin_read_file,
    builtin_write_file,
)
from agent.builtin.capabilities import (
    builtin_biznav_features,
    builtin_file_symbols,
    builtin_symbol_search,
)
from agent.builtin.lightweight import (
    builtin_calculator,
    builtin_json_format,
    builtin_json_parse,
    builtin_regex_match,
    builtin_url_parse,
)
from agent.builtin.extra import (
    builtin_csv_parse,
    builtin_datetime_now,
    builtin_http_get,
    builtin_http_post,
    builtin_text_split,
    builtin_uuid4,
)
from agent.builtin.git import (
    builtin_git_commit,
    builtin_git_diff,
    builtin_git_log,
    builtin_git_status,
)
from agent.builtin.models import BUILTIN_TOOL_NAMES, RiskLevel, ToolResult
from agent.builtin.search import builtin_grep
from agent.builtin.schemas import get_builtin_schema


# 工具风险等级映射（V0 + V1 轻量）
TOOL_RISK_LEVEL: dict[str, RiskLevel] = {
    # V0 Python 5 核心
    "read_file": "read",
    "write_file": "medium",
    "edit_file": "medium",
    "list_dir": "read",
    "grep": "read",
    # V1 Python 5 轻量（全部 risk=low —— 无 I/O 无副作用）
    "calculator": "low",
    "json_parse": "low",
    "json_format": "low",
    "regex_match": "low",
    "url_parse": "low",
    # V1 Rust 工具（仅占位；真实风险等级由 Rust 端 evaluate_hitl 决定）
    "stat_file": "read",
    "mkdir": "medium",       # 创建目录 → 影响文件系统
    "delete_file": "high",   # 删除不可逆
    "move_file": "high",     # 移动不可逆
    "find": "read",
    "glob": "read",
    "hash": "read",
    "base64": "read",
    "shell": "critical",     # shell 命令 —— 高危，需 HITL
    # V3 Python 常用工具（全部 low —— 无副作用 / 受控 I/O）
    "datetime_now": "low",
    "uuid4": "low",
    "http_get": "low",
    "csv_parse": "low",
    "text_split": "low",
    # V4 扩展工具（2026-08-04）
    "http_post": "medium",    # 写接口 → 改变外部系统状态，走 HITL
    "git_status": "read",
    "git_diff": "read",
    "git_log": "read",
    "git_commit": "medium",   # 写仓库历史，走 HITL
    "symbol_search": "read",
    "file_symbols": "read",
    "biznav_features": "read",
}


# 工具描述（注入 LLM system prompt）
TOOL_DESCRIPTIONS: dict[str, str] = {
    "read_file": (
        "Read a file's content. Supports line range (start_line, max_lines). "
        "Returns text content + size/line_count/truncated meta. "
        "For files > 100MB, use logviewer instead."
    ),
    "write_file": (
        "Atomically write content to a file (temp + rename). "
        "Requires HITL approval in production. "
        "Set overwrite=True to overwrite existing files."
    ),
    "edit_file": (
        "Search-replace edit a file. Requires unique match unless replace_all=True. "
        "Requires HITL approval in production."
    ),
    "list_dir": (
        "List a directory's contents (files + subdirs). Returns up to max_entries "
        "(default 1000). Use builtin_stat_file for detailed metadata."
    ),
    "grep": (
        "Search text patterns in files/directories. Supports regex/literal, "
        "case-insensitive, context lines (-C). "
        "Use ripgrep if available (faster). For files > 100MB, use logviewer."
    ),
    "calculator": (
        "AST-safe arithmetic evaluation. Supports + - * / // % ** and unary +/-. "
        "Only int / float literals allowed (no variables, no function calls). "
        "Use for: numerical reasoning, unit conversions (caller's responsibility)."
    ),
    "json_parse": (
        "Parse JSON string with strict error reporting (line / col / char position). "
        "Returns parsed Python object. Use strict=True (default) for production."
    ),
    "json_format": (
        "Format JSON with indent / sort_keys / ensure_ascii control. "
        "Default ensure_ascii=False to preserve Chinese / emoji. "
        "Returns formatted JSON string."
    ),
    "regex_match": (
        "Match regex pattern against text. Returns list of {span, match, groups?, named_groups?}. "
        "ReDoS-safe: pattern length capped at 1024 chars, max 1000 matches returned. "
        "Use builtin_calculator or builtin_url_parse before regex for structured inputs."
    ),
    "url_parse": (
        "Parse URL via urllib.parse. Returns {scheme, netloc, path, params, query, fragment, "
        "hostname, port, username, password, query_dict, ipv4_valid}. "
        "Use for: link extraction, request building, query string inspection."
    ),
    # Rust 工具描述（仅占位；前端 / LLM 由 V1.5 接力 Rust 端 generate_tool_descriptions）
    "stat_file": "Get file metadata (size, mtime, permissions, owner). Read-only.",
    "mkdir": "Create directory (with parents option). Requires HITL approval.",
    "delete_file": "Delete a file or directory (recursive). Requires HITL approval (high risk).",
    "move_file": "Move/rename a file or directory. Requires HITL approval (high risk).",
    "find": "Find files by name pattern (regex/glob). Returns paths. Read-only.",
    "glob": "Glob pattern matching (e.g. **/*.py). Returns sorted paths. Read-only.",
    "hash": "Compute file hash (md5 / sha1 / sha256 / blake2b). Read-only.",
    "base64": "Base64 encode/decode a string or file content. Read-only.",
    "shell": "Execute a shell command. Requires HITL approval (critical risk). Use sparingly.",
    "datetime_now": (
        "Get the current date/time, optional UTC offset hours (e.g. 8 for UTC+8). "
        "Returns ISO 8601 by default. Use for time-sensitive reasoning."
    ),
    "uuid4": "Generate a random v4 UUID string. Use when you need a unique identifier.",
    "http_get": (
        "Perform an HTTP GET request (http/https only, bounded timeout and response size). "
        "Returns status_code, headers, and body (truncated if large). Use for public/internal web APIs."
    ),
    "csv_parse": (
        "Parse CSV text into rows (optional header row, custom delimiter, row limit). "
        "Returns header + rows or plain rows."
    ),
    "text_split": (
        "Split long text into chunks by character count or separator. "
        "Use before summarizing or processing large documents."
    ),
    # V4 扩展工具（CODE/WORK 模式能力补齐，2026-08-04）
    "http_post": (
        "Perform an HTTP POST request with JSON body or form data. "
        "Mutates external system state — requires HITL approval (medium risk). "
        "Use for business API write calls; for read-only fetching prefer http_get."
    ),
    "git_status": (
        "Show git working-tree status (branch + changed files, porcelain format). "
        "Read-only. `repo` is the repository directory path."
    ),
    "git_diff": (
        "Show uncommitted changes as a unified diff. staged=True shows staged "
        "(index vs HEAD) changes; optional path_filter limits to one path. Read-only."
    ),
    "git_log": (
        "Show recent commit history as one-line summaries "
        "(hash, author, date, subject). limit defaults to 20, capped at 200. Read-only."
    ),
    "git_commit": (
        "Commit the currently STAGED changes with the given message (git commit -m). "
        "Does not stage files and never pushes. Requires HITL approval (medium risk)."
    ),
    "symbol_search": (
        "Search the workspace code-symbol index by name (function / class / variable, "
        "exact + fuzzy). Returns symbol locations. Read-only, < 50ms. "
        "Fails gracefully if the workspace index has not been built."
    ),
    "file_symbols": (
        "List all symbols defined in a given source file (from the workspace "
        "code-symbol index). Read-only."
    ),
    "biznav_features": (
        "Query business feature points (业务功能点) of a project from the biznav index, "
        "optionally filtered by category. Read-only."
    ),
}


class BuiltinToolRegistry:
    """内置工具注册表。

    V0 是简单 dict 映射。V1 扩展为：
      - 异步实例方法
      - Rust 工具（通过 Tauri Command 远端调用）
      - 工具描述缓存
    """

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {
            # V0 Python 5 核心
            "read_file": builtin_read_file,
            "write_file": builtin_write_file,
            "edit_file": builtin_edit_file,
            "list_dir": builtin_list_dir,
            "grep": builtin_grep,
            # V1 Python 5 轻量（low risk，无 I/O）
            "calculator": builtin_calculator,
            "json_parse": builtin_json_parse,
            "json_format": builtin_json_format,
            "regex_match": builtin_regex_match,
            "url_parse": builtin_url_parse,
            # V1 Rust 工具：dispatcher 通过 tauri invoke 远端调用；
            # 此处保留 name 入口（返 None 由 dispatcher 识别并切换到 Rust 通道）
            # V1.5 接力真实 Rust 实现；当前 V1.5 之前 Rust 端 mod.rs 仅占位注释
            # V3 Python 常用工具（low risk）
            "datetime_now": builtin_datetime_now,
            "uuid4": builtin_uuid4,
            "http_get": builtin_http_get,
            "csv_parse": builtin_csv_parse,
            "text_split": builtin_text_split,
            # V4 扩展工具（CODE/WORK 模式能力补齐）
            "http_post": builtin_http_post,
            "git_status": builtin_git_status,
            "git_diff": builtin_git_diff,
            "git_log": builtin_git_log,
            "git_commit": builtin_git_commit,
            # V4 内部能力只读入口（codenav / biznav）
            "symbol_search": builtin_symbol_search,
            "file_symbols": builtin_file_symbols,
            "biznav_features": builtin_biznav_features,
        }

    def get(self, name: str) -> Callable[..., Any] | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def risk_level(self, name: str) -> RiskLevel:
        return TOOL_RISK_LEVEL.get(name, "read")

    def has(self, name: str) -> bool:
        return name in self._tools

    def schema(self, name: str) -> dict | None:
        """返回工具参数 JSON Schema（供 planner / 动态工具目录）。"""
        return get_builtin_schema(name)

    def generate_tool_descriptions(self) -> str:
        """生成注入 LLM system prompt 的工具描述片段。"""
        lines = ["You have access to the following built-in tools (run inside the agent, <1ms latency):"]
        for name in BUILTIN_TOOL_NAMES:
            desc = TOOL_DESCRIPTIONS.get(name, "")
            risk = TOOL_RISK_LEVEL.get(name, "read")
            lines.append(f"- builtin_{name}: {desc} [risk={risk}]")
        lines.append("")
        lines.append("To invoke a builtin tool, set call['server']='builtin' and call['name']=tool_name.")
        lines.append("Built-in tools take priority over MCP tools for the same task.")
        return "\n".join(lines)


# ---- 单例工厂（测试可重置）---------------------------------------------------

_DEFAULT_REGISTRY: BuiltinToolRegistry | None = None


def get_default_registry() -> BuiltinToolRegistry:
    """返回默认 registry（单例）。"""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = BuiltinToolRegistry()
    return _DEFAULT_REGISTRY


def reset_default_registry() -> None:
    """测试 hook：重置单例。"""
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None
