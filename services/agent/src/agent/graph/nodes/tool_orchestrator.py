"""tool_orchestrator node —— 动态工具加载与调用循环（2026-08-03）。

包一层 DynamicToolLoop：模型每轮输出一个动作（SELECT_TOOLS / TOOL_CALLS /
REQUEST_FULL_TOOLS / ASK_USER / FINAL_ANSWER），系统按协议执行；写 / 高危调用
暂停等 HITL 审批（hitl_gate），决定后回本节点恢复。

设计文档：docs/superpowers/specs/2026-08-03-dynamic-tool-loop-design.md
"""
from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.llm.router import LMRouter
from agent.tools.catalog import ToolCatalog
from agent.tools.loop import DynamicToolLoop


async def tool_orchestrator_node(
    state: AgentState,
    llm: LMRouter,
    mcp: Any | None = None,
    catalog: Any | None = None,
) -> dict:
    """运行一轮动态工具循环，返回 AgentState 增量。"""
    catalog = catalog or ToolCatalog(mcp=mcp)
    loop = DynamicToolLoop(llm, catalog)
    return await loop.run(state)
