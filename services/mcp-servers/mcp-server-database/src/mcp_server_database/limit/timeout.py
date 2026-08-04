"""Per-call timeout (uses asyncio.wait_for upstream in server.py)."""
from mcp_server_database.config import Settings

DEFAULT_TIMEOUT_SEC = Settings.model_fields["tool_timeout_sec"].default