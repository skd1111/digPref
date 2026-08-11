"""MCP client — discovers tools across servers and routes invocations."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from agent.mcp.registry import ServerRegistry


class McpClient:
    """Aggregates N MCP servers (stdio transport) into one façade.

    Usage:
        async with McpClient(registry) as mcp:
            tools = await mcp.list_tools()
            result = await mcp.invoke({"server": "db", "name": "query", "args": {...}},
                                       timeout_sec=10, row_limit=50)
    """

    def __init__(self, registry: ServerRegistry) -> None:
        self.registry = registry
        self._stack: AsyncExitStack | None = None
        self._sessions: dict[str, ClientSession] = {}

    async def __aenter__(self) -> McpClient:
        self._stack = AsyncExitStack()
        for name, params in self.registry.servers.items():
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._sessions[name] = session
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack:
            await self._stack.aclose()
            self._stack = None
            self._sessions.clear()

    async def list_tools(self) -> list[dict]:
        all_tools: list[dict] = []
        for server, session in self._sessions.items():
            for tool in (await session.list_tools()).tools:
                all_tools.append(
                    {
                        "server": server,
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.inputSchema,
                    }
                )
        return all_tools

    async def invoke(
        self,
        call: dict,
        *,
        timeout_sec: int,
        row_limit: int,
    ) -> Any:
        """Invoke a tool with hard timeout and row_limit appended to args."""
        server = self._sessions[call["server"]]
        args = {**call.get("args", {}), "_row_limit": row_limit}
        return await asyncio.wait_for(
            server.call_tool(call["name"], args),
            timeout=timeout_sec,
        )
