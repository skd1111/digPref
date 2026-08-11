"""Per-host method allow-list (e.g. some hosts allow GET only)."""

from __future__ import annotations

from mcp_server_rest.config import Settings


class MethodNotAllowedError(Exception):
    pass


def assert_method_allowed(method: str, host: str) -> None:
    s = Settings()
    allowed = s.allowed_methods_by_host.get(host)
    if allowed is None:
        # No per-host override → default allow GET only.
        allowed = ["GET"]
    if method.upper() not in [m.upper() for m in allowed]:
        raise MethodNotAllowedError(f"{method} not allowed on {host}; allowed: {allowed}")
