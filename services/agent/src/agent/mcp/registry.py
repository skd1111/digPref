"""Loads mcp.yaml and produces StdioServerParameters per server."""
from __future__ import annotations

from pathlib import Path

import yaml
from mcp import StdioServerParameters


class ServerRegistry:
    def __init__(self, servers: dict[str, StdioServerParameters]) -> None:
        self.servers = servers

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ServerRegistry":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        servers: dict[str, StdioServerParameters] = {}
        for name, cfg in (data or {}).get("servers", {}).items():
            servers[name] = StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args", []),
                env=cfg.get("env"),
            )
        return cls(servers)