"""MCP stdio entry — SSH command execution with safety guards."""
from __future__ import annotations

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool

from mcp_server_ssh.safety import cmd_blacklist, host_whitelist
from mcp_server_ssh.tools import exec, upload


server = Server("mcp-server-ssh")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="ssh.exec",
            description="Execute a shell command on a whitelisted host.",
            inputSchema={
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "command": {"type": "string"},
                    "timeout_sec": {"type": "integer"},
                },
                "required": ["host", "command"],
            },
        ),
        Tool(
            name="ssh.upload",
            description="Upload a local file via SFTP. **Requires HITL**.",
            inputSchema={
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "local_path": {"type": "string"},
                    "remote_path": {"type": "string"},
                    "approval_id": {
                        "type": "string",
                        "description": "HITL approval ID from the upstream gate. Required.",
                    },
                },
                "required": ["host", "local_path", "remote_path", "approval_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> dict:
    host_whitelist.assert_allowed(arguments["host"])
    if name == "ssh.exec":
        cmd_blacklist.assert_safe(arguments["command"])
        return await exec.run(arguments)
    if name == "ssh.upload":
        return await upload.run(arguments)
    raise ValueError(f"unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())