"""Wrapper that turns an MCP tool into a LangChain BaseTool."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from agent.mcp.client import McpClient


def as_langchain_tool(client: McpClient, server: str, name: str, schema: dict) -> BaseTool:
    # TODO: implement BaseTool subclass dynamically
    raise NotImplementedError
