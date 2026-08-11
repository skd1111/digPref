"""MCP stdio entry — Playwright headless browser."""

from __future__ import annotations

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool

from mcp_server_rpa.safety import domain_whitelist
from mcp_server_rpa.tools import click, extract, navigate

server = Server("mcp-server-rpa")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="rpa.navigate",
            description="Navigate to a whitelisted URL.",
            inputSchema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        ),
        Tool(
            name="rpa.click",
            description="Click an element by CSS selector.",
            inputSchema={
                "type": "object",
                "properties": {"selector": {"type": "string"}},
                "required": ["selector"],
            },
        ),
        Tool(
            name="rpa.extract",
            description="Extract page text/HTML by selector.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "mode": {"type": "enum", "enum": ["text", "html", "attr"]},
                    "attr": {"type": "string"},
                },
                "required": ["selector"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> dict:
    if name == "rpa.navigate":
        domain_whitelist.assert_allowed(arguments["url"])
        return await navigate.run(arguments)
    if name == "rpa.click":
        return await click.run(arguments)
    if name == "rpa.extract":
        return await extract.run(arguments)
    raise ValueError(f"unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
