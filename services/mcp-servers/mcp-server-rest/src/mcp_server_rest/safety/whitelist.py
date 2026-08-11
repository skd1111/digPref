"""Hard hostname whitelist — refuses requests to non-listed hosts."""

from __future__ import annotations

from mcp_server_rest.config import Settings


class HostNotAllowedError(Exception):
    pass


def assert_host_allowed(host: str) -> None:
    s = Settings()
    if not s.allowed_hosts:
        # Default-deny if no whitelist configured.
        raise HostNotAllowedError(f"no whitelist configured; refusing {host!r}")
    if host not in s.allowed_hosts:
        raise HostNotAllowedError(f"host {host!r} not in whitelist")
