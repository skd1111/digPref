"""Row limit helper — prefers caller-provided _row_limit, falls back to default."""

from __future__ import annotations


def from_args(args: dict) -> int:
    n = args.get("_row_limit")
    if isinstance(n, int) and n > 0:
        return n
    from mcp_server_database.config import Settings

    return Settings().default_row_limit
