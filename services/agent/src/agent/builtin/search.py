"""Phase 1B · 搜索工具（grep）V0 实现。

V0 范围：
  - builtin_grep: ripgrep 集成（无则降级 Python 实现）
  - 文件大小 ≤100MB（更大走 logviewer）
  - 支持正则 / 字面 / 上下文（-C）

V1 范围：
  - builtin_find / builtin_glob
  - ripgrep 进程池（OnceCell<Command>）
  - 跨平台路径处理
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

from agent.builtin.models import PathOutOfBoundsError, PathSecurityError, ToolResult
from agent.builtin.path_sandbox import validate_path


# 100MB 文件大小限制（与 files.py 保持一致）
_MAX_FILE_BYTES = 100 * 1024 * 1024


async def builtin_grep(
    pattern: str,
    path: str = ".",
    *,
    is_regex: bool = False,
    case_insensitive: bool = False,
    context_lines: int = 0,
    max_results: int = 200,
    allowed_roots: list[str] | None = None,
    encoding: str = "utf-8",
) -> ToolResult:
    """在文件或目录中搜索文本。

    Args:
        pattern: 搜索模式（正则或字面）。
        path: 文件或目录路径（默认 cwd）。
        is_regex: True 时按正则解析。
        case_insensitive: True 时忽略大小写。
        context_lines: 上下文行数（-C）。
        max_results: 最大结果数。
        allowed_roots: 允许的根目录。
        encoding: 文件编码。

    Returns:
        ToolResult(ok=True, content=list[dict], meta={"hit_count", "truncated"})
        每个 dict: {"file", "line_no", "line", "context_before", "context_after"}
    """
    if not pattern:
        return ToolResult(ok=False, error="empty pattern", risk_level="read")

    if is_regex:
        try:
            re.compile(pattern)
        except re.error as exc:
            return ToolResult(
                ok=False,
                error=f"invalid_regex: {exc}",
                risk_level="read",
            )

    try:
        p = validate_path(path, allowed_roots=allowed_roots, must_exist=True)
    except (PathSecurityError, PathOutOfBoundsError, FileNotFoundError) as exc:
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            risk_level="read",
        )

    # 单文件：检查大小
    if p.is_file() and p.stat().st_size > _MAX_FILE_BYTES:
        return ToolResult(
            ok=False,
            error=f"file_too_large: {p.stat().st_size} bytes",
            hint="use logviewer for files > 100MB",
            meta={"size": p.stat().st_size},
            risk_level="read",
        )

    # 优先 ripgrep，无则降级 Python
    if shutil.which("rg"):
        return await _grep_ripgrep(
            pattern, p, is_regex=is_regex, case_insensitive=case_insensitive,
            context_lines=context_lines, max_results=max_results,
        )
    return await _grep_python(
        pattern, p, is_regex=is_regex, case_insensitive=case_insensitive,
        context_lines=context_lines, max_results=max_results, encoding=encoding,
    )


async def _grep_ripgrep(
    pattern: str,
    p: Path,
    *,
    is_regex: bool,
    case_insensitive: bool,
    context_lines: int,
    max_results: int,
) -> ToolResult:
    """ripgrep 实现（JSON 输出解析）。"""
    args = ["rg", "--json", "-C", str(context_lines)]
    if not is_regex:
        args.append("--fixed-strings")
    if case_insensitive:
        args.append("-i")
    args.extend(["--", pattern, str(p)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (asyncio.TimeoutError, OSError) as exc:
        return ToolResult(
            ok=False,
            error=f"ripgrep_failed: {type(exc).__name__}: {exc}",
            risk_level="read",
        )

    # 解析 JSON Lines
    import json
    hits: list[dict] = []
    truncated = False
    for line_bytes in stdout.splitlines():
        if len(hits) >= max_results:
            truncated = True
            break
        try:
            ev = json.loads(line_bytes)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "match":
            data = ev.get("data", {})
            text_obj = data.get("lines", {})
            line_text = text_obj.get("text", "").rstrip("\n")
            hits.append({
                "file": data.get("path", {}).get("text", ""),
                "line_no": data.get("line_number", 0),
                "line": line_text,
                "context_before": [],  # ripgrep --json 不直接带 context
                "context_after": [],
            })

    return ToolResult(
        ok=True,
        content=hits,
        meta={"hit_count": len(hits), "truncated": truncated, "engine": "ripgrep"},
        risk_level="read",
    )


async def _grep_python(
    pattern: str,
    p: Path,
    *,
    is_regex: bool,
    case_insensitive: bool,
    context_lines: int,
    max_results: int,
    encoding: str,
) -> ToolResult:
    """纯 Python 降级实现（ripgrep 不可用时）。"""
    flags = 0 if case_insensitive else re.IGNORECASE
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        return ToolResult(ok=False, error=f"invalid_regex: {exc}", risk_level="read")

    targets: list[Path]
    if p.is_file():
        targets = [p]
    else:
        targets = [e for e in p.rglob("*") if e.is_file()]

    hits: list[dict] = []
    truncated = False

    def _scan_file(target: Path) -> list[dict]:
        try:
            if target.stat().st_size > _MAX_FILE_BYTES:
                return []
            with open(target, "r", encoding=encoding, errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return []
        local: list[dict] = []
        for i, line in enumerate(lines, start=1):
            if compiled.search(line):
                local.append({
                    "file": str(target),
                    "line_no": i,
                    "line": line.rstrip("\n"),
                    "context_before": [
                        lines[j].rstrip("\n")
                        for j in range(max(0, i - 1 - context_lines), i - 1)
                    ],
                    "context_after": [
                        lines[j].rstrip("\n")
                        for j in range(i, min(len(lines), i + context_lines))
                    ],
                })
        return local

    for target in targets:
        if len(hits) >= max_results:
            truncated = True
            break
        local = await asyncio.to_thread(_scan_file, target)
        for h in local:
            if len(hits) >= max_results:
                truncated = True
                break
            hits.append(h)

    return ToolResult(
        ok=True,
        content=hits,
        meta={"hit_count": len(hits), "truncated": truncated, "engine": "python"},
        risk_level="read",
    )
