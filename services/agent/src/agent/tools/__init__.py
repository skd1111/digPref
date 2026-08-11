"""agent.tools —— 动态工具加载与工具调用模块。

组件：
    - ToolCatalog      合并 builtin + MCP 工具目录（摘要 / 完整定义 / 执行）
    - DynamicToolLoop  五动作循环驱动（SELECT_TOOLS / TOOL_CALLS /
                       REQUEST_FULL_TOOLS / ASK_USER / FINAL_ANSWER）

设计文档：docs/superpowers/specs/2026-08-03-dynamic-tool-loop-design.md
"""

from __future__ import annotations

from agent.tools.catalog import ToolCatalog
from agent.tools.loop import DynamicToolLoop

__all__ = ["DynamicToolLoop", "ToolCatalog"]
