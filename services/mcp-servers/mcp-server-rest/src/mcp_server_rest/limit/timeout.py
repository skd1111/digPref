"""Timeout helper."""

from mcp_server_rest.config import Settings


def cap_sec() -> int:
    return Settings().tool_timeout_sec
