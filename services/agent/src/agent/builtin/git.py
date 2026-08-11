"""Phase 1B V4 · Git 工具族（CODE 模式核心工具，2026-08-04）。

工具清单：
    - git_status   工作区状态（只读，read）
    - git_diff     变更 diff（只读，read；staged 参数看暂存区）
    - git_log      提交历史（只读，read）
    - git_commit   提交暂存区变更（medium，受写操作 HITL 治理）

安全约束：
    - 只允许固定子命令白名单，禁止任意 git 参数注入（push / reset /
      checkout 等改变远端或不可逆的子命令一律不暴露）。
    - repo 路径先走 path_sandbox.validate_path() 再执行。
    - subprocess 统一 timeout + 输出截断，避免挂死 / 爆内存。
"""

from __future__ import annotations

import asyncio
import subprocess

from agent.builtin.models import ToolResult
from agent.builtin.path_sandbox import validate_path

# 单次输出上限（字符），超过截断
_MAX_OUTPUT_CHARS = 200_000
# 默认超时（秒）
_DEFAULT_TIMEOUT_SEC = 30


async def _run_git(repo: str, args: list[str], *, risk_level: str) -> ToolResult:
    """在 repo 目录执行 `git <args>`，统一错误处理与输出截断。"""
    try:
        p = validate_path(repo, must_exist=True)
    except Exception as exc:
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            hint="check repo path / allowed_roots",
            risk_level=risk_level,
        )
    if not p.is_dir():
        return ToolResult(ok=False, error="not_a_directory", hint=str(p), risk_level=risk_level)
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            ["git", *args],
            cwd=str(p),
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return ToolResult(
            ok=False,
            error="git_not_found",
            hint="git 可执行文件不在 PATH 中",
            risk_level=risk_level,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            ok=False,
            error="timeout",
            hint=f"git 命令超过 {_DEFAULT_TIMEOUT_SEC}s",
            risk_level=risk_level,
        )
    except Exception as exc:
        return ToolResult.from_exception(exc, risk_level=risk_level)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    truncated = len(stdout) > _MAX_OUTPUT_CHARS
    return ToolResult(
        ok=proc.returncode == 0,
        content={
            "returncode": proc.returncode,
            "stdout": stdout[:_MAX_OUTPUT_CHARS],
            "stderr": stderr[:_MAX_OUTPUT_CHARS],
            "truncated": truncated,
        },
        error=None
        if proc.returncode == 0
        else (stderr.strip()[:2000] or f"exit {proc.returncode}"),
        meta={"returncode": proc.returncode, "truncated": truncated},
        risk_level=risk_level,
    )


async def builtin_git_status(*, repo: str) -> ToolResult:
    """git status --porcelain=v1 -b（分支 + 变更摘要）。"""
    return await _run_git(repo, ["status", "--porcelain=v1", "--branch"], risk_level="read")


async def builtin_git_diff(
    *,
    repo: str,
    staged: bool = False,
    path_filter: str | None = None,
) -> ToolResult:
    """git diff（工作区 vs 暂存区；staged=True 看暂存区 vs HEAD）。"""
    args = ["diff", "--no-color"]
    if staged:
        args.append("--staged")
    if path_filter:
        args += ["--", path_filter]
    return await _run_git(repo, args, risk_level="read")


async def builtin_git_log(*, repo: str, limit: int = 20) -> ToolResult:
    """git log 单行摘要（hash / 作者 / 日期 / 标题）。"""
    n = max(1, min(int(limit), 200))
    args = [
        "log",
        f"-{n}",
        "--pretty=format:%h%x09%an%x09%ad%x09%s",
        "--date=short",
    ]
    return await _run_git(repo, args, risk_level="read")


async def builtin_git_commit(*, repo: str, message: str) -> ToolResult:
    """git commit -m（仅提交已暂存变更；medium 风险走 HITL）。

    不暴露 git add / push —— 暂存由用户在 IDE 内完成，Agent 只负责提交，
    避免误提交无关文件或影响远端。
    """
    if not message or not message.strip():
        return ToolResult(
            ok=False, error="empty_message", hint="提交信息不能为空", risk_level="medium"
        )
    return await _run_git(repo, ["commit", "-m", message.strip()], risk_level="medium")


__all__: list[str] = [
    "builtin_git_commit",
    "builtin_git_diff",
    "builtin_git_log",
    "builtin_git_status",
]
