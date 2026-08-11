"""Phase 1B V7 · 大文件查看与搜索工具（klogg 式只读能力，2026-08-10）。

背景：builtin_read_file / builtin_grep 有 100MB 硬上限（超限提示走 logviewer），
但 Agent 工具链此前没有可直接调用的大文件入口。本模块补齐该缺口：

工具清单（全部只读，risk=read，绝不修改文件）：
    - log_read_lines   按行范围查看大文件（流式扫描，无 100MB 限制；支持 tail）
    - log_search       在大文件中搜索模式（流式扫描，命中 max_results 即早停）

设计要点（对标 klogg 的"打开即看、搜完就走"）：
    - 二进制分块流式读取（io 缓冲逐行），绝不 readlines() 全量加载；
    - 读取到 max_lines 即停、搜索到 max_results 即停（早停 = GB 级也可秒回）；
    - 单行内容截断（_MAX_LINE_CHARS），防超长行爆内存；
    - tail 模式从文件尾反向分块读取，不需要扫描全文；
    - 与 read_file / grep 一致：路径先走 path_sandbox，行号 0-based。
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from typing import Any

from agent.builtin.models import ToolResult
from agent.builtin.path_sandbox import validate_path

# 默认 / 上限
_DEFAULT_READ_LINES = 200
_MAX_READ_LINES = 2000
_DEFAULT_TAIL_LINES = 100
_MAX_TAIL_LINES = 2000
_DEFAULT_MAX_RESULTS = 200
_MAX_RESULTS_CAP = 1000
_MAX_CONTEXT_LINES = 20
_MAX_PATTERN_LEN = 1024  # 与 regex_match 一致（防 ReDoS）
_MAX_LINE_CHARS = 4000  # 单行内容截断上限
_TAIL_CHUNK_BYTES = 256 * 1024  # tail 反向读取块大小


def _open_target(path: str) -> Path | ToolResult:
    """路径沙箱 + 存在性 + 必须是文件。"""
    try:
        p = validate_path(path, must_exist=True)
    except Exception as exc:
        return ToolResult.from_exception(exc, risk_level="read")
    if not p.is_file():
        return ToolResult(ok=False, error="not_a_file", hint=str(p), risk_level="read")
    return p


def _decode_line(raw: bytes, encoding: str) -> str:
    text = raw.decode(encoding, errors="replace").rstrip("\r\n")
    if len(text) > _MAX_LINE_CHARS:
        return text[:_MAX_LINE_CHARS] + "…[truncated]"
    return text


def builtin_log_read_lines(
    *,
    path: str,
    start_line: int = 0,
    max_lines: int = _DEFAULT_READ_LINES,
    tail_lines: int | None = None,
    encoding: str = "utf-8",
) -> ToolResult:
    """查看大文件内容（只读，无 100MB 限制）。

    两种模式：
      - 范围模式（默认）：从 start_line（0-based）起读 max_lines 行，读到即停；
      - tail 模式：传 tail_lines=N 返回文件最后 N 行（从文件尾反向读，不扫全文）。

    meta：reached_eof（是否读到文件尾）；到达 EOF 时附 line_count（总行数）。
    """
    p = _open_target(path)
    if isinstance(p, ToolResult):
        return p

    # ---- tail 模式：反向分块读取 ----
    if tail_lines is not None:
        n = max(1, min(int(tail_lines), _MAX_TAIL_LINES))
        try:
            size = p.stat().st_size
            # 文件以换行结尾时，split 会在末尾产生一个伪空行，需先探测后剔除
            trailing_nl = False
            if size > 0:
                with p.open("rb") as probe_fh:
                    probe_fh.seek(-1, 2)
                    trailing_nl = probe_fh.read(1) == b"\n"
            collected: list[bytes] = []
            remaining = b""
            offset = size
            line_count = 0
            with p.open("rb") as fh:
                while offset > 0 and line_count <= n:
                    step = min(_TAIL_CHUNK_BYTES, offset)
                    offset -= step
                    fh.seek(offset)
                    chunk = fh.read(step) + remaining
                    parts = chunk.split(b"\n")
                    remaining = parts[0]
                    collected = parts[1:] + collected
                    line_count = len(collected) + (1 if remaining else 0)
                if remaining:
                    collected = [remaining, *collected]
            if trailing_nl and collected and collected[-1] == b"":
                collected.pop()
            lines = [_decode_line(raw, encoding) for raw in collected[-n:]]
            return ToolResult(
                ok=True,
                content=lines,
                meta={"mode": "tail", "returned": len(lines), "size": size},
                risk_level="read",
            )
        except Exception as exc:
            return ToolResult.from_exception(exc, risk_level="read")

    # ---- 范围模式：流式扫描到 max_lines 即停 ----
    start = max(0, int(start_line))
    limit = max(1, min(int(max_lines), _MAX_READ_LINES))
    try:
        out_lines: list[str] = []
        total = 0
        reached_eof = True
        with p.open("rb") as fh:
            for raw in fh:
                if total >= start:
                    out_lines.append(_decode_line(raw, encoding))
                    if len(out_lines) >= limit:
                        reached_eof = False  # 后面可能还有行，先探测
                        probe = fh.readline()
                        if not probe:
                            reached_eof = True
                            total += 1
                        break
                total += 1
        meta: dict[str, Any] = {
            "mode": "range",
            "returned": len(out_lines),
            "start_line": start,
            "reached_eof": reached_eof,
        }
        if reached_eof:
            meta["line_count"] = total
        return ToolResult(ok=True, content=out_lines, meta=meta, risk_level="read")
    except Exception as exc:
        return ToolResult.from_exception(exc, risk_level="read")


def builtin_log_search(
    *,
    path: str,
    pattern: str,
    is_regex: bool = False,
    case_insensitive: bool = False,
    context_lines: int = 0,
    max_results: int = _DEFAULT_MAX_RESULTS,
    encoding: str = "utf-8",
) -> ToolResult:
    """在大文件中搜索文本模式（只读，无 100MB 限制，命中上限即早停）。

    返回命中列表，每项 {line_no（0-based）, line, before[], after[]}；
    before/after 为上下文行（context_lines，上限 20）。
    meta：hit_count / truncated / scanned_lines（早停时只扫到首个上限命中处）。
    """
    if not pattern:
        return ToolResult(ok=False, error="empty_pattern", risk_level="read")
    if len(pattern) > _MAX_PATTERN_LEN:
        return ToolResult(
            ok=False,
            error="pattern_too_long",
            hint=f"模式长度上限 {_MAX_PATTERN_LEN} 字符",
            risk_level="read",
        )
    p = _open_target(path)
    if isinstance(p, ToolResult):
        return p

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern if is_regex else re.escape(pattern), flags)
    except re.error as exc:
        return ToolResult(
            ok=False,
            error="invalid_regex",
            hint=str(exc),
            risk_level="read",
        )

    ctx = max(0, min(int(context_lines), _MAX_CONTEXT_LINES))
    limit = max(1, min(int(max_results), _MAX_RESULTS_CAP))

    try:
        hits: list[dict[str, Any]] = []
        before_buf: deque[tuple[int, str]] = deque(maxlen=ctx) if ctx else deque()
        open_after: list[tuple[dict[str, Any], int]] = []  # (hit, 还需补充的 after 行数)
        scanned = 0
        truncated = False

        with p.open("rb") as fh:
            for line_no, raw in enumerate(fh):
                text = _decode_line(raw, encoding)
                # 1) 给已命中条目补 after 上下文
                if open_after:
                    still: list[tuple[dict[str, Any], int]] = []
                    for pending, need in open_after:
                        pending["after"].append(text)
                        if need - 1 > 0:
                            still.append((pending, need - 1))
                    open_after = still
                # 2) 判断命中
                if len(hits) < limit and regex.search(text):
                    hit: dict[str, Any] = {
                        "line_no": line_no,
                        "line": text,
                        "before": [t for _, t in before_buf] if ctx else [],
                        "after": [],
                    }
                    hits.append(hit)
                    if ctx:
                        open_after.append((hit, ctx))
                    if len(hits) >= limit:
                        truncated = True
                        # 补满最后一批 after 上下文后即可早停
                        if not open_after:
                            scanned = line_no + 1
                            break
                elif ctx:
                    before_buf.append((line_no, text))
                scanned = line_no + 1
                if truncated and not open_after:
                    break

        return ToolResult(
            ok=True,
            content=hits,
            meta={
                "hit_count": len(hits),
                "truncated": truncated,
                "scanned_lines": scanned,
                "size": p.stat().st_size,
            },
            risk_level="read",
        )
    except Exception as exc:
        return ToolResult.from_exception(exc, risk_level="read")


__all__: list[str] = [
    "builtin_log_read_lines",
    "builtin_log_search",
]
