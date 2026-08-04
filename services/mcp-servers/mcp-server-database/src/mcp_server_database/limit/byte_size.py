"""Byte-level truncation for column values + whole-result payloads.

Two distinct concerns:
    - per-cell : individual TEXT / BLOB / JSON values may be huge
    - per-result: the entire JSON payload must fit in MCP transport

Both are bounded by environment-tunable defaults; callers can override.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


_DEFAULT_PER_CELL = 8 * 1024          # 8 KB
_DEFAULT_PER_RESULT = 256 * 1024      # 256 KB


@dataclass(frozen=True)
class TruncationConfig:
    per_cell_bytes: int = _DEFAULT_PER_CELL
    per_result_bytes: int = _DEFAULT_PER_RESULT


def from_args(args: dict) -> TruncationConfig:
    return TruncationConfig(
        per_cell_bytes=int(args.get("_per_cell_bytes") or _DEFAULT_PER_CELL),
        per_result_bytes=int(args.get("_per_result_bytes") or _DEFAULT_PER_RESULT),
    )


# ---- Per-cell truncation ---------------------------------------------------

def truncate_cell(value: Any, cap: int) -> tuple[Any, bool]:
    """Return (possibly-truncated-value, was_truncated)."""
    if value is None:
        return None, False
    if isinstance(value, (bytes, bytearray, memoryview)):
        b = bytes(value)
        if len(b) > cap:
            return b[:cap] + b"\n...[truncated]", True
        return b, False
    if isinstance(value, str):
        if len(value.encode("utf-8")) > cap:
            encoded = value.encode("utf-8")[:cap]
            return encoded.decode("utf-8", errors="replace") + "\n…[truncated]", True
        return value, False
    # Fallback: serialise and truncate
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = repr(value)
    if len(s.encode("utf-8")) > cap:
        return s[:cap] + "\n…[truncated]", True
    return value, False


def truncate_row(row: list[Any], cfg: TruncationConfig) -> tuple[list[Any], bool]:
    out: list[Any] = []
    truncated = False
    for v in row:
        new_v, was = truncate_cell(v, cfg.per_cell_bytes)
        out.append(new_v)
        truncated = truncated or was
    return out, truncated


def truncate_rows(rows: list[list[Any]], cfg: TruncationConfig) -> tuple[list[list[Any]], bool, int]:
    """Truncate rows, then truncate the whole payload if it still exceeds per_result_bytes.

    Returns (rows, was_truncated_at_least_once, dropped_rows_count).
    """
    truncated_any = False
    out: list[list[Any]] = []
    for row in rows:
        new_row, was = truncate_row(row, cfg)
        out.append(new_row)
        truncated_any = truncated_any or was

    # Whole-result cap: encode and measure
    encoded_size = sum(
        len(json.dumps(r, ensure_ascii=False, default=str).encode("utf-8"))
        for r in out
    )
    dropped = 0
    if encoded_size > cfg.per_result_bytes:
        kept: list[list[Any]] = []
        running = 0
        for row in out:
            row_size = len(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
            if running + row_size > cfg.per_result_bytes:
                dropped = len(out) - len(kept)
                break
            kept.append(row)
            running += row_size
        out = kept
        truncated_any = True
    return out, truncated_any, dropped