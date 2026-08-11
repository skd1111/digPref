"""Tests for byte-level truncation."""

from __future__ import annotations

import json

from mcp_server_database.limit.byte_size import (
    TruncationConfig,
    truncate_cell,
    truncate_row,
    truncate_rows,
)


class TestTruncateCell:
    def test_none(self):
        assert truncate_cell(None, 100) == (None, False)

    def test_short_string(self):
        assert truncate_cell("hello", 100) == ("hello", False)

    def test_long_string_truncated(self):
        s = "x" * 500
        out, was = truncate_cell(s, 100)
        assert was is True
        assert out.endswith("…[truncated]")

    def test_bytes_truncated(self):
        b = b"\x00" * 500
        out, was = truncate_cell(b, 100)
        assert was is True
        assert isinstance(out, bytes)

    def test_object_serialised(self):
        big_obj = {"a": "x" * 200}
        out, was = truncate_cell(big_obj, 100)
        assert was is True
        assert isinstance(out, str)


class TestTruncateRow:
    def test_row_mixed(self):
        row = ["short", "x" * 500, None, 42]
        out, was = truncate_row(row, TruncationConfig(per_cell_bytes=50))
        assert was is True
        assert out[0] == "short"
        assert out[2] is None
        assert out[3] == 42


class TestTruncateRows:
    def test_drops_rows_over_byte_cap(self):
        # Build rows that are individually small but add up to >1KB
        rows = [["x" * 100] for _ in range(20)]  # ~100 * 20 = 2000 bytes
        out, was, dropped = truncate_rows(
            rows, TruncationConfig(per_cell_bytes=10_000, per_result_bytes=500)
        )
        assert was is True
        assert dropped > 0
        # Total bytes must be within cap (approximately)
        encoded = sum(len(json.dumps(r).encode()) for r in out)
        assert encoded <= 500

    def test_no_truncation_when_fits(self):
        rows = [["a"], ["b"], ["c"]]
        out, was, dropped = truncate_rows(
            rows, TruncationConfig(per_cell_bytes=1000, per_result_bytes=10_000)
        )
        assert was is False
        assert dropped == 0
        assert len(out) == 3
