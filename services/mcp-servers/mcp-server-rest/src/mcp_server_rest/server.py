"""MCP stdio entry — registers tools and starts the server."""
from __future__ import annotations

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool

from mcp_server_rest.safety import method_policy, whitelist
from mcp_server_rest.tools import request, spec


server = Server("mcp-server-rest")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="rest.request",
            description="Execute a whitelisted HTTP request.",
            inputSchema={
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "method": {"type": "enum", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string"},
                    "headers": {"type": "object"},
                    "body": {"type": "object"},
                },
                "required": ["host", "method", "path"],
            },
        ),
        Tool(
            name="rest.openapi_to_tools",
            description="Convert an OpenAPI document into a list of MCP tool definitions.",
            inputSchema={"type": "object", "properties": {"url": {"type": "string"}}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> dict:
    if name == "rest.request":
        whitelist.assert_host_allowed(arguments["host"])
        method_policy.assert_method_allowed(arguments["method"], arguments["host"])
        return await request.run(arguments)
    if name == "rest.openapi_to_tools":
        return await spec.to_tools(arguments["url"])
    raise ValueError(f"unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())