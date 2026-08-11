"""Hard domain whitelist — refuses navigation to non-listed domains."""

from __future__ import annotations

from urllib.parse import urlparse

from mcp_server_rpa.config import Settings


class DomainNotAllowedError(Exception):
    pass


def assert_allowed(url: str) -> None:
    host = urlparse(url).hostname or ""
    allowed = Settings().allowed_domains
    if not allowed:
        raise DomainNotAllowedError(f"no rpa domain whitelist configured; refusing {host!r}")
    if not any(host == d or host.endswith(f".{d}") for d in allowed):
        raise DomainNotAllowedError(f"domain {host!r} not in whitelist")
