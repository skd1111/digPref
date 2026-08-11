"""Connection pool — keyed by logical name (configured via env/secret vault)."""

from __future__ import annotations

from mcp_server_database.config import Settings


class Connections:
    """Thin façade — agent should always inject DSN via the upstream Agent,
    which pulls it from the Rust-side credential vault.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def dsn(self, name: str) -> str:
        # Look in env first: EAIDE_DB_DSN_<NAME>
        import os

        env = os.environ.get(f"EAIDE_DB_DSN_{name.upper()}")
        if env:
            return env
        if name in self._settings.connections:
            return self._settings.connections[name]
        raise KeyError(f"unknown connection: {name}")
