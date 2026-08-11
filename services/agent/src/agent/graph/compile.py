"""Compile the LangGraph state machine — single entrypoint.

We compose the StateGraph by binding our dependencies (llm, mcp) at
compile time via a `Runtime` carrier. This keeps the graph object
self-contained and trivially mockable in tests.

Phase 16：每个节点经 `_with_trace` 包装 —— 执行完成后把中文思考/工具调用
记录到思维链（trace.db thinking_steps）。best-effort，不影响主图。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph

from agent.config import settings
from agent.dual.policy import tag_plan_with_policy  # Phase 18 双框架
from agent.dual.router import mode_router_node  # Phase 18 双框架
from agent.graph.edges import (
    route_after_decompose,
    route_after_hitl,
    route_after_repair,
    route_after_tool,
    route_after_tool_loop,
)
from agent.graph.nodes.decompose import decompose_node
from agent.graph.nodes.hitl_gate import hitl_gate_node
from agent.graph.nodes.intent import intent_node
from agent.graph.nodes.local_intent import local_intent_node  # Phase 4 V1
from agent.graph.nodes.planner import planner_node
from agent.graph.nodes.rag_retrieve import rag_retrieve_node  # Phase 4 V1
from agent.graph.nodes.repair import repair_node
from agent.graph.nodes.responder import responder_node
from agent.graph.nodes.tool_orchestrator import tool_orchestrator_node
from agent.graph.nodes.tool_runner import tool_runner_node
from agent.graph.nodes.vision_understand import vision_understand_node  # Phase 4 V0
from agent.graph.state import AgentState


@dataclass
class Runtime:
    llm: object
    mcp: object


def _with_trace(node_name: str, fn):
    """Phase 16 LangGraph Hook：节点执行后记录思维链步骤。

    best-effort：记录失败绝不影响主图执行；`trace_enabled=False` 时直接跳过。
    后端不区分工作模式一律记录（金融合规审计；前端仅开发模式渲染）。
    """

    async def wrapped(s):
        t0 = time.monotonic()
        out = await fn(s)
        try:
            if getattr(settings, "trace_enabled", True):
                session_id = s.get("run_id") if isinstance(s, dict) else None
                if session_id and isinstance(out, dict):
                    from agent.trace.collector import get_collector

                    latency = int((time.monotonic() - t0) * 1000)
                    await get_collector().record_node_step(
                        session_id, node_name, out, latency_ms=latency
                    )
        except Exception:
            pass  # best-effort
        return out

    return wrapped


def build_graph(runtime: Runtime) -> StateGraph:
    g = StateGraph(AgentState)
    rt = runtime  # closure capture

    async def _intent(s):
        return await intent_node(s, rt.llm)

    async def _mode_router(s):  # Phase 18：双框架模式路由（START 后第一站）
        return await mode_router_node(s, rt.llm)

    async def _planner(s):
        return await planner_node(s, rt.llm, rt.mcp)

    async def _decompose(s):
        out = await decompose_node(s, rt.llm, mcp=rt.mcp)
        # Phase 18：既有 plan 时逐子任务打 ExecutionPolicy（与 plan 同序）
        plan = s.get("plan") or []
        if plan and isinstance(out, dict) and "execution_policies" not in out:
            out["execution_policies"] = tag_plan_with_policy(
                plan,
                routing=s.get("routing") or "work",
                autonomy=s.get("autonomy") or "interactive",
            )
        return out

    async def _tool_orchestrator(s):
        return await tool_orchestrator_node(s, rt.llm, mcp=rt.mcp)

    async def _tool_runner(s):
        return await tool_runner_node(s, rt.mcp)

    async def _hitl_gate(s):
        return await hitl_gate_node(s, rt.llm)

    async def _repair(s):
        return await repair_node(s, rt.llm)

    async def _responder(s):
        # 2026-08-04：不再向最终回复注入路由声明 / 结构化执行报告 ——
        # 用户侧只展示纯回答。路由决策与审批信号仍按 Phase 18 留痕审计
        # （dual/router.py + trace.db），仅从用户可见文本中移除。
        return await responder_node(s, rt.llm)

    # Phase 4 V0
    async def _vision_understand(s):
        return await vision_understand_node(s, rt.llm)

    # Phase 4 V1
    async def _local_intent(s):
        # local_intent_node 与 intent_node 互斥；当前仅注册不接主图
        # （V1.5 由 settings 决定是否替换 intent_node）
        return await local_intent_node(s, rt.llm)

    async def _rag_retrieve(s):
        return await rag_retrieve_node(s, None)  # 用默认 retriever

    g.add_node(
        "vision_understand", _with_trace("vision_understand", _vision_understand)
    )  # Phase 4 V0
    g.add_node("mode_router", _with_trace("mode_router", _mode_router))  # Phase 18
    g.add_node("intent", _with_trace("intent", _intent))
    g.add_node("planner", _with_trace("planner", _planner))
    g.add_node("decompose", _with_trace("decompose", _decompose))
    g.add_node("tool_orchestrator", _with_trace("tool_orchestrator", _tool_orchestrator))
    g.add_node("tool_runner", _with_trace("tool_runner", _tool_runner))
    g.add_node("hitl_gate", _with_trace("hitl_gate", _hitl_gate))
    g.add_node("repair", _with_trace("repair", _repair))
    g.add_node("responder", _with_trace("responder", _responder))
    # Phase 4 V1 注册（不默认接主图；保留给 settings.rag_enabled=True 时启用）
    g.add_node("local_intent", _with_trace("local_intent", _local_intent))
    g.add_node("rag_retrieve", _with_trace("rag_retrieve", _rag_retrieve))

    # START → mode_router → intent → （可选 rag_retrieve）
    g.add_edge(START, "mode_router")
    g.add_edge("mode_router", "intent")
    tool_loop = getattr(settings, "tool_loop_enabled", True)
    if tool_loop:
        # 2026-08-03 动态工具循环主路径：intent → rag → decompose → tool_orchestrator
        if getattr(settings, "rag_enabled", True):
            g.add_edge("intent", "rag_retrieve")
            g.add_edge("rag_retrieve", "decompose")
        else:
            g.add_edge("intent", "decompose")
        g.add_conditional_edges(
            "decompose",
            route_after_decompose,
            {
                "tool_orchestrator": "tool_orchestrator",
                "tool_runner": "tool_runner",  # 保留目标：回退路径/单测
                "responder": "responder",
            },
        )
        g.add_conditional_edges(
            "tool_orchestrator",
            route_after_tool_loop,
            {
                "tool_orchestrator": "tool_orchestrator",
                "hitl_gate": "hitl_gate",
                "responder": "responder",
            },
        )
        g.add_conditional_edges(
            "hitl_gate",
            route_after_hitl,
            {
                "hitl_gate": "hitl_gate",
                "tool_orchestrator": "tool_orchestrator",
                "tool_runner": "tool_runner",
                "responder": "responder",
            },
        )
    else:
        # 既有路径（测试 / 回退）：intent → rag → planner → decompose → tool_runner
        if getattr(settings, "rag_enabled", True):
            g.add_edge("intent", "rag_retrieve")
            g.add_edge("rag_retrieve", "planner")
        else:
            g.add_edge("intent", "planner")
        g.add_edge("planner", "decompose")
        g.add_conditional_edges(
            "decompose",
            route_after_decompose,
            {
                "tool_orchestrator": "tool_orchestrator",
                "tool_runner": "tool_runner",
                "responder": "responder",
            },
        )
        g.add_conditional_edges(
            "tool_runner",
            route_after_tool,
            {
                "repair": "repair",
                "hitl_gate": "hitl_gate",
            },
        )
        g.add_conditional_edges(
            "repair",
            route_after_repair,
            {
                "tool_runner": "tool_runner",
                "responder": "responder",
            },
        )
        g.add_conditional_edges(
            "hitl_gate",
            route_after_hitl,
            {
                "hitl_gate": "hitl_gate",
                "tool_orchestrator": "tool_orchestrator",
                "tool_runner": "tool_runner",
                "responder": "responder",
            },
        )
    g.add_edge("responder", END)
    return g


def compile_graph(runtime: Runtime, *, checkpointer=None):
    """Return a compiled, runnable graph.

    Parameters
    ----------
    runtime : Runtime
        Carries the LLM router and MCP client.
    checkpointer : optional
        LangGraph checkpointer for HITL state persistence
        (defaults to in-memory for dev; use Redis/Postgres in prod).
    """
    g = build_graph(runtime)
    return g.compile(checkpointer=checkpointer)
