"""Thin httpx async client wrapper."""
from __future__ import annotations

import httpx


def build_client(timeout_sec: int = 10) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout_sec,
        follow_redirects=False,
        headers={"User-Agent": "eaide-rest-mcp/0.1"},
    )