"""rest.request — perform the actual HTTP call."""
from __future__ import annotations

import json

from mcp_server_rest.client import build_client
from mcp_server_rest.config import Settings


async def run(args: dict) -> dict:
    s = Settings()
    async with build_client(s.tool_timeout_sec) as c:
        url = f"https://{args['host']}{args['path']}"
        body = args.get("body")
        if body is not None:
            body = _truncate(body, s.max_body_bytes)
        resp = await c.request(
            args["method"].upper(),
            url,
            headers=args.get("headers") or {},
            json=body,
        )
        return {
            "ok": resp.is_success,
            "status": resp.status_code,
            "headers": dict(resp.headers),
            "body": _truncate(resp.text, s.max_body_bytes),
            "truncated": len(resp.text) > s.max_body_bytes,
        }


def _truncate(s: str, n: int) -> str:
    if isinstance(s, (dict, list)):
        s = json.dumps(s)
    if len(s) > n:
        return s[:n] + f"\n…[truncated, {n} bytes]"
    return s