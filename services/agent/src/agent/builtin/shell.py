"""Phase 1B V2 · shell 工具（Python 原生兜底实现）。

用途：Agent 独立运行（无 Tauri 运行时注入）时，dispatcher 对 `builtin_shell`
走本实现；桌面壳集成时可切换到 Rust 端 `builtin_shell`（Tauri Command）。

安全策略（与 Rust 端 execute_shell 严格镜像）：
    1. 危险操作符拦截（; & | < > ` $ ( ) 换行 等）
    2. 首 token 白名单（支持 `git*` 通配前缀）
    3. 长度上限 4096
    4. 超时强杀（subprocess.TimeoutExpired → timed_out=True）
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import sys

from agent.builtin.models import ToolResult

# 危险操作符 —— 出现即拒绝（防止命令注入 / 管道 / 重定向）
DANGEROUS_SHELL_CHARS: tuple[str, ...] = (
    ";",
    "&",
    "|",
    "<",
    ">",
    "`",
    "$",
    "(",
    ")",
    "{",
    "}",
    "\n",
    "\r",
    "\x00",
)

# shell 命令最大字节数
SHELL_MAX_BYTES: int = 4096


async def builtin_shell(
    command: str,
    *,
    allowed_prefixes: list[str] | None = None,
    timeout_sec: int = 30,
) -> ToolResult:
    """执行白名单 shell 命令（critical 风险 —— 永远需要 HITL 审批后调用）。"""
    trimmed = (command or "").strip()
    if not trimmed:
        return ToolResult(ok=False, error="empty_command", risk_level="critical")
    if len(trimmed) > SHELL_MAX_BYTES:
        return ToolResult(
            ok=False,
            error=f"command_too_long: {len(trimmed)} > {SHELL_MAX_BYTES}",
            risk_level="critical",
        )
    for ch in DANGEROUS_SHELL_CHARS:
        if ch in trimmed:
            return ToolResult(
                ok=False,
                error=f"dangerous_operator: {ch!r} not allowed in shell command",
                risk_level="critical",
            )

    first = shlex.split(trimmed)[0] if shlex.split(trimmed) else ""
    allowed = allowed_prefixes or []
    if allowed and not any(
        first.startswith(p.rstrip("*")) if p.endswith("*") else first == p for p in allowed
    ):
        return ToolResult(
            ok=False,
            error=f"command_not_allowed: {first} (allowed: {allowed})",
            risk_level="critical",
        )

    timeout = timeout_sec if timeout_sec and timeout_sec > 0 else 30

    def _run() -> tuple[int, str, str, bool]:
        """平台 shell 执行（Windows → cmd /C；Unix → /bin/sh -c）。"""
        shell_cmd: list[str]
        if sys.platform == "win32":
            shell_cmd = ["cmd", "/C", trimmed]
        else:
            shell_cmd = ["/bin/sh", "-c", trimmed]
        try:
            proc = subprocess.run(
                shell_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return (
                proc.returncode,
                proc.stdout or "",
                proc.stderr or "",
                False,
            )
        except subprocess.TimeoutExpired as exc:
            return (
                124,
                (exc.stdout or "").decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or ""),
                (exc.stderr or "").decode("utf-8", "replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or ""),
                True,
            )
        except OSError as exc:
            raise exc

    try:
        exit_code, stdout, stderr, timed_out = await asyncio.to_thread(_run)
    except OSError as exc:
        return ToolResult(
            ok=False,
            error=f"spawn_failed: {type(exc).__name__}: {exc}",
            risk_level="critical",
        )

    return ToolResult(
        ok=True,
        content={
            "command": trimmed,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
        },
        risk_level="critical",
    )
