"""Phase 1B · 原生工具层（Builtin Core Tools Layer）—— V2 公开 API。

内置工具直接跑在 Agent 进程内（不走 MCP），延迟 <1ms，离线可用，
由路径沙箱 + HITL 严格受控。

V1 增量（2026-07-30）：
  - 5 轻量工具（calculator / json_parse / json_format / regex_match / url_parse）
  - Rust 9 工具占位（stat_file / mkdir / delete_file / move_file / find / glob / hash / base64 / shell）
  - 审计双 schema tool_calls 表（Python + Rust 同步）
  - SSE 三处同步 3 新事件（builtin_tool_started / done / denied）

V2 增量（2026-07-31）：
  - Rust 9/9 工具真实实现（delete_file / move_file / shell + md5/sha1/blake2b + glob crate）
  - 真实 HITL 前置闸门（审批前不执行，接 hitl_gate_node）
  - Tauri IPC 桥完整调用（超时 + 重试 + 运行时注入）
  - 3 高危工具的 Python 原生兜底（Agent 独立运行可用）
"""
from __future__ import annotations

from agent.builtin._tauri_runtime import (
    TauriRuntimeClient,
    clear_tauri_runtime,
    get_tauri_app_handle,
    is_tauri_runtime_available,
    set_tauri_runtime,
)
from agent.builtin.dispatcher import ToolDispatcher, dispatcher
from agent.builtin.files import (
    builtin_delete_file,
    builtin_edit_file,
    builtin_list_dir,
    builtin_move_file,
    builtin_read_file,
    builtin_write_file,
)
from agent.builtin.lightweight import (
    builtin_calculator,
    builtin_json_format,
    builtin_json_parse,
    builtin_regex_match,
    builtin_url_parse,
)
from agent.builtin.models import (
    BUILTIN_TOOL_NAMES,
    BuiltinTool,
    PathSecurityError,
    PathOutOfBoundsError,
    RiskLevel,
    ToolResult,
    is_rust_tool,
    RUST_TOOL_NAMES,
)
from agent.builtin.path_sandbox import validate_path
from agent.builtin.registry import BuiltinToolRegistry, get_default_registry
from agent.builtin.search import builtin_grep
from agent.builtin.shell import builtin_shell
from agent.builtin.tauri_bridge import (
    invoke_rust_tool_sync,
    has_python_fallback,
    is_implemented as is_rust_tool_implemented,
    is_v1_5_implemented as is_rust_tool_v1_5_implemented,
    is_v2_implemented as is_rust_tool_v2_implemented,
)

# V1 公开 API 列表（30+ 项）
__all__ = [
    # 核心数据类
    "ToolResult",
    "BuiltinTool",
    "RiskLevel",
    "PathSecurityError",
    "PathOutOfBoundsError",
    # 工具注册
    "BuiltinToolRegistry",
    "get_default_registry",
    "BUILTIN_TOOL_NAMES",
    "RUST_TOOL_NAMES",
    "is_rust_tool",
    # V0 5 工具
    "builtin_read_file",
    "builtin_write_file",
    "builtin_edit_file",
    "builtin_list_dir",
    "builtin_grep",
    # V1 5 轻量工具
    "builtin_calculator",
    "builtin_json_parse",
    "builtin_json_format",
    "builtin_regex_match",
    "builtin_url_parse",
    # V2 高危工具（Python 原生兜底）
    "builtin_delete_file",
    "builtin_move_file",
    "builtin_shell",
    # 路径沙箱
    "validate_path",
    # Tauri 运行时注入
    "TauriRuntimeClient",
    "set_tauri_runtime",
    "get_tauri_app_handle",
    "clear_tauri_runtime",
    "is_tauri_runtime_available",
    # V2 Rust IPC 桥接
    "invoke_rust_tool_sync",
    "is_rust_tool_v1_5_implemented",
    "is_rust_tool_v2_implemented",
    "is_rust_tool_implemented",
    "has_python_fallback",
    # 调度器
    "ToolDispatcher",
    "dispatcher",
]
