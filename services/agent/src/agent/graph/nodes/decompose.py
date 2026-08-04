"""decompose node —— 编排决策器：Agent 自动判断任务由谁执行（Phase 12 V2）。

按「子智能体启用决策提示词」运行，mode ∈ {MAIN_AGENT, TOOL_ONLY,
SINGLE_SUBAGENT, MULTI_SUBAGENT, ASK_USER, REFUSE}，并遵守安全门槛：

    - REFUSE / ASK_USER / 需用户确认 → 不执行、不派生，由 responder 输出说明；
    - 只读分析子智能体（plan / summarise / custom / data_summary）才可以派生；
      写操作 / 工具调用永远留在主图 tool_runner + hitl_gate 单线完成；
    - 判定失败 / LLM 不可用 / 决策不合规 → 保守回退单 Agent（原行为不变）。
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from agent.config import settings
from agent.graph.state import AgentState, record_trace
from agent.llm.router import LMRouter
from agent.orchestrator.orchestrator import get_orchestrator
from agent.orchestrator.spec import ContextPolicy, ModelPolicy, SubAgentSpec
from agent.tools.catalog import ToolCatalog


# 可用子智能体目录 —— 编排决策器只能从这里选（不虚构能力）。
# 本实现中每个档案都映射到只读分析型 LLM worker（task_type 白名单）。
AVAILABLE_SUBAGENT_CATALOG: list[dict] = [
    {
        "name": "analysis_agent",
        "description": "通用分析 / 推理子智能体：复杂推理、综合分析、结构化总结。",
        "capabilities": ["analysis", "reasoning", "summarization"],
    },
    {
        "name": "plan_agent",
        "description": "规划子智能体：方案设计、步骤拆解、执行计划生成。",
        "capabilities": ["planning", "decomposition"],
    },
    {
        "name": "data_summary_agent",
        "description": "数据摘要子智能体：数据集 / 表格摘要与洞察提取（本地执行）。",
        "capabilities": ["data_summary", "insight"],
    },
    {
        "name": "research_agent",
        "description": "调研子智能体：资料收集、对比分析、调研报告生成。",
        "capabilities": ["research", "comparison", "report"],
    },
]


async def decompose_node(
    state: AgentState,
    llm: LMRouter,
    orch: Any | None = None,
    mcp: Any | None = None,
) -> dict:
    """运行编排决策器；需要子智能体则并行派生并汇总回报。"""
    intent = state.get("intent") or "query"
    user_prompt = state.get("user_prompt", "")
    plan = state.get("plan") or []
    history = state.get("messages", [])

    # 1) 快速跳过：开关关闭 / 无需编排的意图
    if not getattr(settings, "multi_agent_auto_enabled", True):
        return _skip("multi_agent disabled by config")
    if intent == "chitchat":
        return _skip("chitchat never decomposed")
    if intent == "mutate":
        # 写操作：走工具（内置/MCP 写工具经 HITL 审批），不派生子智能体
        if getattr(settings, "tool_loop_enabled", True):
            return {
                "decompose_decision": _tool_only_decision(
                    "写操作任务 → 动态工具循环 + HITL 审批",
                ),
                "multi_agent": False,
                "sub_agent_reports": [],
                "trace": [record_trace("decompose", "ok", mode="TOOL_ONLY", reason="write intent")],
            }
        return _skip(f"intent={intent} never decomposed")

    # 2) 收集决策器运行时输入（可用工具 / 权限 / 成本 / 安全策略）
    tool_specs = await _list_tools(mcp)

    # 3) 调用编排决策器；任何异常 / 缺方法 / fallback → 保守单 Agent
    decision = await _decide(state, llm, tool_specs)
    if decision is None:
        return _skip("judge failed or conservative fallback")

    inner = decision.get("decision") or {}
    mode = str(inner.get("mode") or "MAIN_AGENT")
    reason = str(inner.get("reason") or "")

    # 4) 终态模式：追问 / 拒绝 → responder 直接输出（不执行、不派生）
    if mode in ("ASK_USER", "REFUSE"):
        return {
            "decompose_decision": decision,
            "multi_agent": False,
            "sub_agent_reports": [],
            "trace": [record_trace("decompose", "ok", mode=mode, reason=reason)],
        }

    # 5) 高风险确认门槛：未经用户明确确认不得执行
    if inner.get("user_confirmation_required") is True:
        return {
            "decompose_decision": decision,
            "multi_agent": False,
            "sub_agent_reports": [],
            "trace": [record_trace(
                "decompose", "skipped",
                reason=f"confirmation_required: {inner.get('confirmation_message') or ''}",
            )],
        }

    # 6) 单 Agent 模式：主智能体直接回答 / 主智能体调用工具
    if mode in ("MAIN_AGENT", "TOOL_ONLY"):
        return {
            "decompose_decision": decision,
            "multi_agent": False,
            "sub_agent_reports": [],
            "trace": [record_trace("decompose", "ok", mode=mode, reason=reason)],
        }

    # 7) SINGLE_SUBAGENT / MULTI_SUBAGENT：并行派生子智能体
    subagents = [
        s for s in (decision.get("selected_subagents") or []) if isinstance(s, dict)
    ]
    if not subagents:
        return _skip("no selected subagents")

    orch = orch or get_orchestrator()
    run_id = state.get("run_id") or f"run-{uuid.uuid4().hex[:8]}"
    specs = _build_specs(run_id, user_prompt, plan, subagents)
    if not specs:
        return _skip("no valid sub-agent specs")

    results = await asyncio.gather(
        *(orch.spawn(spec) for spec in specs),
        return_exceptions=True,
    )
    reports: list[dict] = []
    for item in results:
        if isinstance(item, Exception):
            reports.append({
                "status": "err",
                "summary": "",
                "error_message": f"{type(item).__name__}: {item}",
                "confidence": 0.0,
                "latency_ms": 0,
            })
        else:
            reports.append(_serialise_report(item))

    return {
        "multi_agent": True,
        "decompose_decision": decision,
        "sub_agent_reports": reports,
        "trace": [record_trace(
            "decompose", "ok",
            mode=mode,
            sub_agents=len(specs),
            reason=reason,
        )],
    }


# ---- Helpers ---------------------------------------------------------------


async def _decide(state: AgentState, llm: LMRouter, tool_specs: list[dict]) -> dict | None:
    """调用编排决策器并校验返回；失败 / fallback 返回 None（调用方走单 Agent）。"""
    try:
        decision = await llm.decompose(
            user_prompt=state.get("user_prompt", ""),
            plan=state.get("plan") or [],
            history=state.get("messages", []),
            available_subagents=AVAILABLE_SUBAGENT_CATALOG,
            available_tools=tool_specs,
            user_permissions={
                "can_read": True,
                "can_write": bool(getattr(settings, "require_hitl_for_write", True)),
            },
            cost_latency_policy={
                "max_subagents": getattr(settings, "multi_agent_max_subtasks", 6),
                "task_timeout_sec": getattr(settings, "orchestrator_task_timeout_sec", 30),
            },
            safety_policy={
                "writes_require_hitl": True,
                "sensitive_local_only": True,
                "tree_max_depth": 2,
                "tree_max_nodes": 30,
                "subagent_read_only": True,
            },
        )
    except Exception:  # noqa: BLE001 —— 判定失败不阻塞主流程
        return None
    if not isinstance(decision, dict) or decision.get("_fallback"):
        return None
    if not isinstance(decision.get("decision"), dict):
        return None
    return decision


async def _list_tools(mcp: Any) -> list[dict]:
    """用 ToolCatalog 拿统一工具摘要（builtin + MCP）；不可用返回空列表。"""
    try:
        return await ToolCatalog(mcp=mcp).summaries()
    except Exception:  # noqa: BLE001
        return []


def _tool_only_decision(reason: str) -> dict:
    """构造 TOOL_ONLY 决策（路由到动态工具循环）。"""
    return {
        "decision": {
            "mode": "TOOL_ONLY",
            "should_enable_subagent": False,
            "execution_allowed": True,
            "user_confirmation_required": False,
            "confidence": 1.0,
            "reason": reason,
            "clarifying_questions": [],
            "confirmation_message": None,
            "refusal_message": None,
        },
        "scoring": {},
        "selected_subagents": [],
        "tool_calls": [],
        "plan": [],
        "fallback": "tool loop",
    }


def _skip(reason: str) -> dict:
    return {
        "multi_agent": False,
        "decompose_decision": None,
        "sub_agent_reports": [],
        "trace": [record_trace("decompose", "skipped", reason=reason)],
    }


def _build_specs(
    run_id: str,
    user_prompt: str,
    plan: list[dict],
    subagents: list[dict],
) -> list[SubAgentSpec]:
    """把决策器选中的子智能体转成只读分析型 SubAgentSpec（depth=1）。"""
    specs: list[SubAgentSpec] = []
    for i, sub in enumerate(subagents, start=1):
        name = str(sub.get("name") or f"sub{i}")
        role = str(sub.get("role") or "analysis")
        task = str(sub.get("task") or sub.get("task_description") or "")
        if not task.strip():
            continue
        task_type = _map_task_type(name, role)
        inputs = sub.get("inputs") if isinstance(sub.get("inputs"), dict) else {}
        exec_fields = {
            "name": name,
            "role": role,
            "user_goal": str(inputs.get("user_goal") or user_prompt),
            "task": task,
            "inputs": inputs,
            "allowed_tools": (
                sub.get("allowed_tools")
                if isinstance(sub.get("allowed_tools"), list) else []
            ),
            "expected_output": str(sub.get("expected_output") or ""),
            "stop_condition": str(sub.get("stop_condition") or ""),
            "safety_policy": {
                "read_only": True,
                "writes_require_hitl": True,
                "sensitive_local_only": True,
            },
        }
        specs.append(SubAgentSpec(
            spec_version=1,
            sub_agent_id=f"{run_id}-sub{i}",
            parent_run_id=run_id,
            parent_sub_agent_id=None,
            depth=1,
            task_type=task_type,
            task_description=task,
            input_payload={
                "source_run_id": run_id,
                "parent_task_description": user_prompt,
                "parent_plan": plan,
                "subagent_role": role,
                "execution_template_fields": exec_fields,
            },
            context_policy=ContextPolicy(
                strategy="passthrough",
                required_fields=[],
                shared_keys=[],
                max_summary_tokens=500,
            ),
            model_policy=ModelPolicy(
                role="execution",
                task_type=task_type,
                carries_sensitive_payload=False,
                preferred_backend=None,
            ),
            requires_write=False,
        ))
    return specs


def _map_task_type(name: str, role: str) -> str:
    """把决策器选中的子智能体映射到只读分析 task_type（未知一律 custom）。"""
    haystack = f"{name} {role}".lower()
    if "plan" in haystack:
        return "plan"
    if any(k in haystack for k in ("summar", "report", "research")):
        return "summarise"
    if "data" in haystack:
        return "data_summary"
    return "custom"


def _serialise_report(report: Any) -> dict:
    try:
        return report.model_dump(mode="json")
    except AttributeError:
        return {"status": "err", "summary": "", "error_message": str(report)}
