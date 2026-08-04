"""Body-size limit helper."""
from mcp_server_rest.config import Settings


def cap_bytes() -> int:
    return Settings().max_body_bytes