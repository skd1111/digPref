"""loganalysis.extractor —— Phase 2F+ V1 ERROR 块提取 + 日志级别检测。

设计：
- extract_error_blocks(lines, max_stack_lines=50)：从全文按行扫，提取连续 ERROR 段
- detect_level(line)：识别单行的日志级别（ERROR / WARN / INFO / DEBUG / TRACE / FATAL）
- 启发式规则：
    - ERROR 头：行首或时间戳后含 ERROR / FATAL / SEVERE / Exception / [ERR]
    - 堆栈延续：缩进（\\t 或 4+ 空格）/ at ... / Caused by:
    - 同 stack fingerprint 去重（保留首次出现）

调用方：
- api.py：先用 extractor 切块 → scrubber 脱敏 → LLM 根因分析
- log_level_classify：本模块的 detect_level 提供正则兜底（无 LLM 时）
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Iterable

from agent.loganalysis.models import (
    ALL_LEVELS,
    LEVEL_DEBUG,
    LEVEL_ERROR,
    LEVEL_FATAL,
    LEVEL_INFO,
    LEVEL_TRACE,
    LEVEL_WARN,
    ErrorBlock,
)

# ---- ERROR 头识别 ---------------------------------------------------------

# 匹配 ERROR / FATAL / SEVERE / Exception / Error: / [ERR]
# 允许行首有 ISO 时间戳（如 "2026-07-29T10:00:00.123 ERROR ..."）
_ERROR_HEADER_RE = re.compile(
    r"(?:^|\s)(?:ERROR|FATAL|SEVERE|Exception|Error:|\[ERR(?:OR)?\])",
    re.IGNORECASE,
)

# 堆栈延续：缩进 / at ... / Caused by:
_STACK_CONT_RE = re.compile(r"^(?:\s{4,}|\t+|\s*at\s|\s*Caused by:|\s*\.{3}\s\d+\smore)")


# ---- 日志级别识别 ---------------------------------------------------------

_LEVEL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:FATAL|SEVERE)\b", re.IGNORECASE), LEVEL_FATAL),
    (re.compile(r"\b(?:ERROR|ERR|Exception)\b", re.IGNORECASE), LEVEL_ERROR),
    (re.compile(r"\b(?:WARN|WARNING)\b", re.IGNORECASE), LEVEL_WARN),
    (re.compile(r"\b(?:INFO|NOTICE)\b", re.IGNORECASE), LEVEL_INFO),
    (re.compile(r"\b(?:DEBUG)\b", re.IGNORECASE), LEVEL_DEBUG),
    (re.compile(r"\b(?:TRACE|FINE)\b", re.IGNORECASE), LEVEL_TRACE),
)


def detect_level(line: str) -> str:
    """单行日志级别识别（启发式，正则优先；fallback INFO）。"""
    if not line:
        return LEVEL_INFO
    for pattern, level in _LEVEL_PATTERNS:
        if pattern.search(line):
            return level
    return LEVEL_INFO


# ---- ERROR 块提取 ---------------------------------------------------------


def extract_error_blocks(
    lines: list[str] | Iterable[str],
    *,
    max_stack_lines: int = 50,
    max_blocks: int = 200,
) -> list[ErrorBlock]:
    """从 lines 提取 ERROR 块。

    流程：
        1. 逐行扫描：ERROR 头 → 开新块；其余行若属于某块（堆栈延续）→ 追加；否则关闭当前块
        2. 块超过 `max_stack_lines` → 自动关闭
        3. fingerprint（adler32）去重 —— 已见过同 fingerprint 的块丢弃
        4. 块数超过 `max_blocks` → 截断

    Args:
        lines: 全行（list[str] / Iterable[str]；函数内部只迭代一次）
        max_stack_lines: 单块堆栈上限（默认 50；架构师红线）
        max_blocks: 总块数上限（默认 200）

    Returns:
        ErrorBlock 列表（按出现顺序；已去重）
    """
    out: list[ErrorBlock] = []
    seen_fingerprints: set[str] = set()
    cur: ErrorBlock | None = None
    cur_level = LEVEL_ERROR

    # 一次性物化为 list（保持 1-based 索引稳定）
    if not isinstance(lines, list):
        lines = list(lines)

    for idx, raw_line in enumerate(lines):
        line_no = idx + 1  # 1-based
        line = raw_line if isinstance(raw_line, str) else str(raw_line)
        if not line:
            continue
        # 1. ERROR 头检测
        if _ERROR_HEADER_RE.search(line):
            # 关闭旧块
            if cur is not None:
                _finalize_block(cur, seen_fingerprints, out)
                if len(out) >= max_blocks:
                    cur = None
                    break
            cur = ErrorBlock(
                start_line=line_no,
                end_line=line_no,
                header=line,
                stack_trace=[line],
                level=LEVEL_ERROR,
                fingerprint="",
            )
            cur_level = detect_level(line)
            if cur_level != LEVEL_INFO:
                cur.level = cur_level
            continue

        # 2. 堆栈延续：仅当 line 真的是堆栈 / 上下文时追加
        if cur is not None:
            if _STACK_CONT_RE.match(line):
                if len(cur.stack_trace) < max_stack_lines:
                    cur.stack_trace.append(line)
                    cur.end_line = line_no
                else:
                    # 块超 max_stack_lines → 关闭
                    _finalize_block(cur, seen_fingerprints, out)
                    if len(out) >= max_blocks:
                        cur = None
                        break
                    cur = None
            else:
                # 非堆栈延续行 → 关闭当前块（INFO / DEBUG 等分隔符）
                _finalize_block(cur, seen_fingerprints, out)
                if len(out) >= max_blocks:
                    cur = None
                    break
                cur = None

    # 收尾
    if cur is not None and len(out) < max_blocks:
        _finalize_block(cur, seen_fingerprints, out)

    return out


def _finalize_block(
    block: ErrorBlock,
    seen: set[str],
    out: list[ErrorBlock],
) -> None:
    """收尾块：算 fingerprint → 去重 → append。"""
    fp = _stack_fingerprint(block.stack_trace)
    if fp in seen:
        return
    block.fingerprint = fp
    seen.add(fp)
    out.append(block)


def _stack_fingerprint(stack: list[str]) -> str:
    blob = "\n".join(stack).encode("utf-8", errors="replace")
    return f"{zlib.adler32(blob) & 0xFFFFFFFF:08x}"


# ---- 工具函数 -------------------------------------------------------------


def level_to_color_hint(level: str) -> str:
    """把日志级别映射为前端着色的 hex hint（仅返回色名，不强制前端用）。"""
    return {
        LEVEL_DEBUG: "gray",
        LEVEL_TRACE: "gray",
        LEVEL_INFO: "blue",
        LEVEL_WARN: "yellow",
        LEVEL_ERROR: "red",
        LEVEL_FATAL: "red",
    }.get(level, "gray")


def assert_known_level(level: str) -> str:
    """assert level 是已知的；否则返 LEVEL_INFO。"""
    return level if level in ALL_LEVELS else LEVEL_INFO
