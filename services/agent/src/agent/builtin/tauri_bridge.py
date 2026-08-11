"""Phase 1B V2 · Python → Rust Tauri IPC 桥接。

设计哲学：
    1. Python dispatcher 识别 Rust 工具后调 `invoke_rust_tool_sync()`。
    2. 桥接器通过注入的 Tauri 运行时客户端（`_tauri_runtime.get_tauri_app_handle()`）
       调用 Rust 端 `builtin_*` Tauri Command。
    3. **运行时不可用**（无注入 / Agent 独立运行 / 测试环境）→ 返 None，
       dispatcher 对 V2 三高危工具走 Python 原生兜底，其余返 not_implemented。
    4. 注入由 FastAPI lifespan（`main.py`）与桌面壳集成方负责。

V2 增量（2026-07-31）：
    - 9 个 Rust 工具全部实现标记（is_implemented）
    - 真实 IPC 调用：按工具构造参数 → runtime.invoke → ToolResult
    - 超时（asyncio.wait_for）+ 1 次重试
    - require_hitl 透传：dispatcher 审批通过后传 False 放行执行

协议：
    Rust 端 builtin_* 命令接收 args 结构体（path / allowed_roots / pattern /
    algorithm / mode / require_hitl 等），返回 ToolResult dict
    （ok / content / error / hint / meta / needs_hitl / risk_level）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent.builtin._tauri_runtime import (
    get_tauri_app_handle,
)
from agent.builtin.models import ToolResult

# V1.5 已实现的 Rust 工具名（历史集合，用于兼容判断）
_V1_5_IMPLEMENTED_RUST_TOOLS: frozenset[str] = frozenset(
    {
        "stat_file",
        "mkdir",
        "find",
        "glob",
        "hash",
        "base64",
    }
)

# V2 已实现的 Rust 工具名（9/9 全部真实实现；与 Rust 端 is_v2_implemented 镜像）
_V2_IMPLEMENTED_RUST_TOOLS: frozenset[str] = frozenset(
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

# V3：Rust 工具 Python 原生兜底（运行时不可用时 dispatcher 直接本地执行）。
# 3 个高危（V2）+ 5 个只读（V3，stat/find/glob/hash/base64）+ 1 个写入（mkdir）。
_V2_PYTHON_FALLBACK_TOOLS: frozenset[str] = frozenset(
    {
        "delete_file",
        "move_file",
        "shell",
        "stat_file",
        "find",
        "glob",
        "hash",
        "base64",
        "mkdir",
    }
)

# 默认 IPC 超时（秒）与重试次数
DEFAULT_IPC_TIMEOUT_SEC: float = 30.0
IPC_MAX_RETRIES: int = 1


def is_v1_5_implemented(tool_name: str) -> bool:
    """判断 Rust 工具是否 V1.5 已实现（历史标记，6 工具）。"""
    return tool_name in _V1_5_IMPLEMENTED_RUST_TOOLS


def is_v2_implemented(tool_name: str) -> bool:
    """判断 Rust 工具是否 V2 已实现（9/9）。"""
    return tool_name in _V2_IMPLEMENTED_RUST_TOOLS


def is_implemented(tool_name: str) -> bool:
    """当前实现标记（V2 后 = 9/9）。"""
    return is_v2_implemented(tool_name)


def has_python_fallback(tool_name: str) -> bool:
    """判断 Rust 工具是否有 Python 原生兜底（3 高危 + 5 只读 + mkdir = 9 工具）。"""
    return tool_name in _V2_PYTHON_FALLBACK_TOOLS


def build_rust_args(
    tool_name: str,
    args: dict[str, Any],
    *,
    require_hitl: bool = True,
) -> dict[str, Any]:
    """把 dispatcher 的通用 args 映射为 Rust 端 Command 参数结构。"""
    allowed_roots = args.get("allowed_roots") or []
    base: dict[str, Any] = {}
    if tool_name == "stat_file":
        base = {"path": args.get("path", ""), "allowed_roots": allowed_roots}
    elif tool_name == "mkdir":
        base = {
            "path": args.get("path", ""),
            "parents": bool(args.get("parents", True)),
            "allowed_roots": allowed_roots,
            "require_hitl": require_hitl,
        }
    elif tool_name == "delete_file":
        base = {
            "path": args.get("path", ""),
            "recursive": bool(args.get("recursive", False)),
            "allowed_roots": allowed_roots,
            "require_hitl": require_hitl,
        }
    elif tool_name == "move_file":
        base = {
            "src": args.get("src", ""),
            "dest": args.get("dest", ""),
            "overwrite": bool(args.get("overwrite", False)),
            "allowed_roots": allowed_roots,
            "require_hitl": require_hitl,
        }
    elif tool_name == "find":
        base = {
            "path": args.get("path", ""),
            "pattern": args.get("pattern", ""),
            "regex": bool(args.get("regex", False)),
            "max_results": int(args.get("max_results", 1000)),
            "allowed_roots": allowed_roots,
        }
    elif tool_name == "glob":
        base = {
            "pattern": args.get("pattern", ""),
            "root": args.get("root", ""),
            "max_results": int(args.get("max_results", 1000)),
            "allowed_roots": allowed_roots,
        }
    elif tool_name == "hash":
        base = {
            "path": args.get("path", ""),
            "algorithm": args.get("algorithm", "sha256"),
            "allowed_roots": allowed_roots,
        }
    elif tool_name == "base64":
        base = {
            "data": args.get("data", ""),
            "mode": args.get("mode", "encode"),
            "allowed_roots": allowed_roots,
        }
    elif tool_name == "shell":
        base = {
            "command": args.get("command", ""),
            "allowed_prefixes": list(args.get("allowed_prefixes") or []),
            "timeout_sec": int(args.get("timeout_sec", 30)),
            "require_hitl": require_hitl,
        }
    return base


def _result_from_dict(payload: dict[str, Any], risk_level: str) -> ToolResult:
    """把 Rust 端 ToolResult dict 转成 Python ToolResult（字段对齐）。"""
    return ToolResult(
        ok=bool(payload.get("ok", False)),
        content=payload.get("content"),
        error=payload.get("error"),
        hint=payload.get("hint"),
        meta=payload.get("meta") or {},
        needs_hitl=bool(payload.get("needs_hitl", False)),
        risk_level=payload.get("risk_level") or risk_level,
    )


async def invoke_rust_tool_sync(
    *,
    tool_name: str,
    args: dict[str, Any],
    risk_level: str,
    require_hitl: bool = True,
    timeout_sec: float | None = None,
) -> ToolResult | None:
    """通过注入的 Tauri 运行时调用 Rust 端 builtin_* 命令。

    Args:
        tool_name: Rust 工具名（不含 builtin_ 前缀）。
        args: 工具参数。
        risk_level: 风险等级（ToolResult 兜底）。
        require_hitl: 审批是否仍要求 HITL（审批通过后传 False 放行执行）。
        timeout_sec: IPC 超时（默认 DEFAULT_IPC_TIMEOUT_SEC）。

    Returns:
        ToolResult —— 成功调用 Rust 端后的结果。
        None —— 工具未实现 / 运行时不可用 / 调用失败（best-effort，dispatcher 兜底）。
    """
    if not is_implemented(tool_name):
        return None
    runtime = get_tauri_app_handle()
    if runtime is None:
        return None

    command = f"builtin_{tool_name}"
    command_args = build_rust_args(tool_name, args, require_hitl=require_hitl)
    timeout = timeout_sec if timeout_sec is not None else DEFAULT_IPC_TIMEOUT_SEC

    last_error: Exception | None = None
    for _attempt in range(IPC_MAX_RETRIES + 1):
        try:
            payload = await asyncio.wait_for(
                runtime.invoke(command, command_args),
                timeout=timeout,
            )
            if not isinstance(payload, dict):
                return None
            return _result_from_dict(payload, risk_level)
        except asyncio.TimeoutError as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
            # 非超时错误不重试
            break
    # 全部失败 → 返 None（dispatcher 走 Python 兜底 / not_implemented）
    if last_error is not None:
        return None
    return None
