"""Phase 1B · 文件操作工具（read / write / edit / list_dir / delete / move）。

所有工具先走 path_sandbox.validate_path() 再操作。write/edit 触发风险等级
medium → 由 dispatcher 决定是否走 HITL；delete/move 为 V2 高危工具（high），
永远先过 HITL 审批再执行。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from agent.builtin.models import (
    PathOutOfBoundsError,
    PathSecurityError,
    ToolResult,
)
from agent.builtin.path_sandbox import validate_path

# 100MB 文件大小限制（超过走 logviewer）
_MAX_FILE_BYTES = 100 * 1024 * 1024


# ---- read_file ----------------------------------------------------------------


async def builtin_read_file(
    path: str,
    *,
    start_line: int = 0,
    max_lines: int | None = None,
    encoding: str = "utf-8",
    allowed_roots: list[str] | None = None,
) -> ToolResult:
    """读取文件内容。

    Args:
        path: 文件路径。
        start_line: 起始行（0-based 含）。
        max_lines: 最大行数（None = 全部）。
        encoding: 编码（默认 utf-8）。
        allowed_roots: 允许的根目录（V0 可选）。

    Returns:
        ToolResult(ok=True, content=<str>, meta={"size", "line_count", "truncated"})
    """
    try:
        p = validate_path(path, allowed_roots=allowed_roots, must_exist=True)
    except (PathSecurityError, PathOutOfBoundsError, FileNotFoundError) as exc:
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            hint="check path / allowed_roots",
            risk_level="read",
        )

    size = p.stat().st_size
    if size > _MAX_FILE_BYTES:
        return ToolResult(
            ok=False,
            error=f"file_too_large: {size} bytes",
            hint="use builtin_log_read_lines for files > 100MB",
            meta={"size": size},
            risk_level="read",
        )

    # 用 asyncio.to_thread 避免阻塞事件循环
    def _read() -> tuple[str, int, bool]:
        with open(p, encoding=encoding, errors="replace") as f:
            all_lines = f.readlines()
        truncated = False
        if start_line >= len(all_lines):
            return "", 0, False
        end = len(all_lines) if max_lines is None else min(start_line + max_lines, len(all_lines))
        if max_lines is not None and start_line + max_lines < len(all_lines):
            truncated = True
        return "".join(all_lines[start_line:end]), end - start_line, truncated

    content, line_count, truncated = await asyncio.to_thread(_read)

    return ToolResult(
        ok=True,
        content=content,
        meta={
            "size": size,
            "line_count": line_count,
            "truncated": truncated,
            "start_line": start_line,
        },
        risk_level="read",
    )


# ---- write_file ---------------------------------------------------------------


async def builtin_write_file(
    path: str,
    content: str,
    *,
    encoding: str = "utf-8",
    allowed_roots: list[str] | None = None,
    overwrite: bool = False,
) -> ToolResult:
    """原子写入文件（temp + rename）。

    Args:
        path: 目标文件路径。
        content: 写入内容。
        encoding: 编码。
        allowed_roots: 允许的根目录。
        overwrite: 是否允许覆盖（False 时文件存在返回错误）。

    Returns:
        ToolResult(ok=True, meta={"bytes_written", "path"})
        needs_hitl=True（dispatcher 决定是否触发 HITL）
    """
    try:
        p = validate_path(path, allowed_roots=allowed_roots, must_exist=False)
    except (PathSecurityError, PathOutOfBoundsError) as exc:
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            risk_level="medium",
            needs_hitl=False,
        )

    if p.exists() and not overwrite:
        return ToolResult(
            ok=False,
            error="file_exists: set overwrite=True to overwrite",
            risk_level="medium",
        )

    # 原子写入：temp + rename
    def _write() -> int:
        parent = p.parent
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=parent,
            prefix=f".{p.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        os.replace(tmp_path, p)
        return len(content.encode(encoding))

    try:
        bytes_written = await asyncio.to_thread(_write)
    except OSError as exc:
        return ToolResult(
            ok=False,
            error=f"write_failed: {type(exc).__name__}: {exc}",
            risk_level="medium",
        )

    return ToolResult(
        ok=True,
        content=None,
        meta={"bytes_written": bytes_written, "path": str(p)},
        risk_level="medium",
        needs_hitl=True,  # dispatcher 据此决定是否走 HITL
    )


# ---- edit_file ----------------------------------------------------------------


async def builtin_edit_file(
    path: str,
    search_text: str,
    replace_text: str,
    *,
    encoding: str = "utf-8",
    allowed_roots: list[str] | None = None,
    replace_all: bool = False,
) -> ToolResult:
    """Search-replace 编辑文件。

    Args:
        path: 目标文件路径。
        search_text: 搜索文本。
        replace_text: 替换文本。
        encoding: 编码。
        allowed_roots: 允许的根目录。
        replace_all: 是否替换全部（False 时要求唯一匹配）。

    Returns:
        ToolResult(ok=True, meta={"replacements", "path"})
        needs_hitl=True
    """
    if not search_text:
        return ToolResult(
            ok=False,
            error="empty search_text",
            risk_level="medium",
        )

    try:
        p = validate_path(path, allowed_roots=allowed_roots, must_exist=True)
    except (PathSecurityError, PathOutOfBoundsError, FileNotFoundError) as exc:
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            risk_level="medium",
        )

    def _edit() -> tuple[int, str]:
        original = p.read_text(encoding=encoding)
        count = original.count(search_text)
        if count == 0:
            return 0, original
        if not replace_all and count > 1:
            return -1, original  # -1 表示多匹配未 replace_all
        new_content = original.replace(search_text, replace_text)
        return count, new_content

    try:
        count, new_content = await asyncio.to_thread(_edit)
    except OSError as exc:
        return ToolResult(
            ok=False,
            error=f"read_failed: {type(exc).__name__}: {exc}",
            risk_level="medium",
        )

    if count == 0:
        return ToolResult(
            ok=False,
            error="no_match: search_text not found",
            meta={"search_len": len(search_text)},
            risk_level="medium",
        )
    if count == -1:
        return ToolResult(
            ok=False,
            error="ambiguous_match: multiple matches without replace_all",
            meta={"match_count": -1},
            risk_level="medium",
        )

    # 写回（同样原子）
    def _write() -> None:
        parent = p.parent
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=parent,
            prefix=f".{p.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(new_content)
            tmp_path = tmp.name
        os.replace(tmp_path, p)

    try:
        await asyncio.to_thread(_write)
    except OSError as exc:
        return ToolResult(
            ok=False,
            error=f"write_failed: {type(exc).__name__}: {exc}",
            risk_level="medium",
        )

    return ToolResult(
        ok=True,
        meta={
            "replacements": count,
            "path": str(p),
            "replace_all": replace_all,
        },
        risk_level="medium",
        needs_hitl=True,
    )


# ---- list_dir ------------------------------------------------------------------


async def builtin_list_dir(
    path: str,
    *,
    allowed_roots: list[str] | None = None,
    max_entries: int = 1000,
) -> ToolResult:
    """列出目录。

    Args:
        path: 目录路径。
        allowed_roots: 允许的根目录。
        max_entries: 最大条目数（防误爆）。

    Returns:
        ToolResult(ok=True, content=list[dict], meta={"count", "truncated"})
    """
    try:
        p = validate_path(path, allowed_roots=allowed_roots, must_exist=True)
    except (PathSecurityError, PathOutOfBoundsError, FileNotFoundError) as exc:
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            risk_level="read",
        )

    if not p.is_dir():
        return ToolResult(
            ok=False,
            error="not_a_directory",
            risk_level="read",
        )

    def _list() -> tuple[list[dict], bool]:
        entries: list[dict] = []
        truncated = False
        for entry in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if len(entries) >= max_entries:
                truncated = True
                break
            try:
                stat = entry.stat()
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "is_file": entry.is_file(),
                        "is_dir": entry.is_dir(),
                        "size": stat.st_size if entry.is_file() else None,
                        "mtime": stat.st_mtime,
                    }
                )
            except OSError:
                # 跳过不可访问的条目
                continue
        return entries, truncated

    entries, truncated = await asyncio.to_thread(_list)

    return ToolResult(
        ok=True,
        content=entries,
        meta={
            "count": len(entries),
            "truncated": truncated,
            "path": str(p),
        },
        risk_level="read",
    )


# ---- delete_file / move_file（V2 高危工具 · Python 原生兜底）----------------


async def builtin_delete_file(
    path: str,
    *,
    recursive: bool = False,
    allowed_roots: list[str] | None = None,
) -> ToolResult:
    """删除文件 / 目录（high 风险 —— 必须先过 HITL 审批再调用）。

    Args:
        path: 目标路径。
        recursive: 删除目录时是否递归（目录必须 recursive=True）。
        allowed_roots: 允许的根目录。

    Returns:
        ToolResult(ok=True, content={"path", "removed", "is_dir"})
    """
    try:
        p = validate_path(path, allowed_roots=allowed_roots, must_exist=True)
    except (PathSecurityError, PathOutOfBoundsError, FileNotFoundError) as exc:
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            risk_level="high",
        )

    # 禁止删除 allowed_roots 根目录本身（防止误删整个工作区）
    if allowed_roots:
        for root in allowed_roots:
            if p == Path(root).expanduser().resolve(strict=False):
                return ToolResult(
                    ok=False,
                    error=f"delete_root_forbidden: {p}",
                    risk_level="high",
                )

    is_dir = p.is_dir()
    if is_dir and not recursive:
        return ToolResult(
            ok=False,
            error=f"is_directory: {p} (pass recursive=True)",
            risk_level="high",
        )

    def _delete() -> None:
        if is_dir:
            shutil.rmtree(p)
        else:
            p.unlink()

    try:
        await asyncio.to_thread(_delete)
    except OSError as exc:
        return ToolResult(
            ok=False,
            error=f"delete_failed: {type(exc).__name__}: {exc}",
            risk_level="high",
        )

    return ToolResult(
        ok=True,
        content={"path": str(p), "removed": True, "is_dir": is_dir},
        risk_level="high",
    )


async def builtin_move_file(
    src: str,
    dest: str,
    *,
    overwrite: bool = False,
    allowed_roots: list[str] | None = None,
) -> ToolResult:
    """移动 / 重命名文件或目录（high 风险 —— 必须先过 HITL 审批再调用）。

    Args:
        src: 源路径。
        dest: 目标路径。
        overwrite: 目标已存在时是否覆盖（默认 False）。
        allowed_roots: 允许的根目录。

    Returns:
        ToolResult(ok=True, content={"src", "dest", "moved", "cross_device"})
    """
    try:
        src_p = validate_path(src, allowed_roots=allowed_roots, must_exist=True)
        dest_p = validate_path(dest, allowed_roots=allowed_roots, must_exist=False)
    except (PathSecurityError, PathOutOfBoundsError, FileNotFoundError) as exc:
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            risk_level="high",
        )

    if dest_p.exists() and not overwrite:
        return ToolResult(
            ok=False,
            error=f"dest_exists: {dest_p}",
            risk_level="high",
        )

    cross_device = False

    def _move() -> None:
        nonlocal cross_device
        try:
            os.replace(src_p, dest_p)
        except OSError:
            # 跨卷 / Windows rename 失败 → copy + remove 降级
            cross_device = True
            if src_p.is_dir():
                shutil.copytree(src_p, dest_p, dirs_exist_ok=overwrite)
                shutil.rmtree(src_p)
            else:
                shutil.copy2(src_p, dest_p)
                src_p.unlink()

    try:
        await asyncio.to_thread(_move)
    except OSError as exc:
        return ToolResult(
            ok=False,
            error=f"move_failed: {type(exc).__name__}: {exc}",
            risk_level="high",
        )

    return ToolResult(
        ok=True,
        content={
            "src": str(src_p),
            "dest": str(dest_p),
            "moved": True,
            "cross_device": cross_device,
        },
        risk_level="high",
    )
