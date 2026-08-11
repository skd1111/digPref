"""planner node — produce an ordered list of tool calls.

The planner is given the live MCP tool catalogue so it can produce calls
that match the schema exactly. The tool catalogue is fetched lazily on
first plan and cached for the lifetime of the graph.
"""

from __future__ import annotations

import time

from agent.graph.state import AgentState, record_trace
from agent.llm.router import LMRouter
from agent.mcp.client import McpClient


async def planner_node(
    state: AgentState,
    llm: LMRouter,
    mcp: McpClient,
) -> dict:
    intent = state.get("intent") or "query"
    user_prompt = state.get("user_prompt", "")
    history = state.get("messages", [])

    tool_specs = await _get_tool_specs(mcp)
    if not tool_specs:
        return {
            "plan": [],
            "plan_explanation": "no tools available",
            "trace": [record_trace("planner", "fail", reason="no_tools")],
        }

    try:
        steps, explanation = await llm.plan(
            intent=intent,
            user_prompt=user_prompt,
            history=history,
            tool_specs=tool_specs,
        )
    except Exception as exc:
        return {
            "plan": [],
            "plan_explanation": f"planner error: {exc}",
            "trace": [record_trace("planner", "fail", error=str(exc))],
        }

    # Normalise every step; reject ones that don't match the registry.
    normalised: list[dict] = []
    for step in steps:
        step = _normalise_step(step, tool_specs)
        if step:
            normalised.append(step)

    return {
        "plan": normalised,
        "plan_explanation": explanation,
        "current_step_index": 0,
        "trace": [record_trace("planner", "ok", steps=len(normalised), explanation=explanation)],
    }


# ---- Helpers ---------------------------------------------------------------

# ---- TTL 缓存：5 分钟内复用工具目录，避免重复 MCP list_tools 调用 ----
# 使用闭包变量 + asyncio.Lock 保护并发访问。
# 不用 @lru_cache 是因为它对 async 函数缓存的是 coroutine 对象而非结果。
import asyncio as _asyncio

_CACHE_TTL = 300  # 秒
_cache_result: tuple[dict, ...] | None = None
_cache_expiry: float = 0.0
_cache_lock = _asyncio.Lock()


def reset_tool_specs_cache() -> None:
    """清空工具目录缓存。

    生产环境不需要主动调用（TTL 到期自动刷新）；测试隔离必须调用——
    否则前一个用例用某个 MCP mock 填的目录会泄漏到下一个用不同 mock 的用例，
    导致 _normalise_step 用错目录过滤步骤（详见 BUGFIX_LOG）。
    conftest.py 的 autouse fixture 每个用例前调用它。
    """
    global _cache_result, _cache_expiry
    _cache_result = None
    _cache_expiry = 0.0


async def _get_tool_specs(mcp: McpClient) -> tuple[dict, ...]:
    """返回 MCP 工具规格的不可变元组。

    结果缓存 5 分钟（TTL），过期后自动刷新。
    使用 asyncio.Lock 保护并发读/写，防止竞态条件。
    """
    global _cache_result, _cache_expiry
    # 快速路径：无锁读（常见情况——缓存命中）
    now_mono = time.monotonic()
    if _cache_result is not None and now_mono < _cache_expiry:
        return _cache_result

    # 慢速路径：持锁刷新缓存
    async with _cache_lock:
        # 双重检查：可能其他协程已刷新
        if _cache_result is not None and time.monotonic() < _cache_expiry:
            return _cache_result
        try:
            specs = await mcp.list_tools()
        except Exception:
            return ()
        # 只缓存可序列化的字段（去除 callable）
        _cache_result = tuple(
            {
                "server": s.get("server"),
                "name": s.get("name"),
                "description": s.get("description", ""),
                "inputSchema": s.get("inputSchema", {}),
            }
            for s in specs
        )
        _cache_expiry = time.monotonic() + _CACHE_TTL
        return _cache_result


def _normalise_step(step: dict, tool_specs: tuple) -> dict | None:
    """Validate and complete a planner step against the registry."""
    if not isinstance(step, dict):
        return None
    server = step.get("server")
    name = step.get("name")
    if not server or not name:
        return None
    match = next(
        (s for s in tool_specs if s["server"] == server and s["name"] == name),
        None,
    )
    if not match:
        return None
    return {
        "server": server,
        "name": name,
        "args": step.get("args") or {},
        "risk_level": step.get("risk_level", "read"),
        "rationale": step.get("rationale", ""),
    }
