"""rest.openapi_to_tools — parse an OpenAPI doc into MCP tool definitions.

This is what makes the system extensible: an enterprise can publish its
internal OpenAPI spec and the Agent immediately gets N new tools.
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from mcp_server_rest.safety import whitelist


async def to_tools(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"only http/https URLs are allowed, got {parsed.scheme!r}")
    whitelist.assert_host_allowed(parsed.hostname)

    async with httpx.AsyncClient(timeout=20) as c:
        spec = (await c.get(url)).json()

    out: list[dict] = []
    for path, methods in (spec.get("paths") or {}).items():
        for method, op in (methods or {}).items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            out.append({
                "name": f"api_{method.lower()}_{path}".replace("/", "_").replace("{", "").replace("}", ""),
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary", ""),
            })
    return {"ok": True, "tools": out}