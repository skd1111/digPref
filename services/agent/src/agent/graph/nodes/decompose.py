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


# 时间 / 日期类问题关键词 —— 命中即直接路由动态工具循环（datetime_now），
# 跳过编排决策器 LLM 调用（本地模型单次 10s+，纯浪费）。
_TIME_QUERY_KEYWORDS: tuple[str, ...] = (
    "几号",
    "几月",
    "几日",
    "今天",
    "昨天",
    "明天",
    "前天",
    "后天",
    "现在几点",
    "几点",
    "当前时间",
    "当前日期",
    "今天的日期",
    "星期几",
    "周几",
    "礼拜几",
    "农历",
    "阴历",
    "老黄历",
    "黄历",
    "节气",
)


def _is_time_query(prompt: str) -> bool:
    """短提示词命中时间/日期关键词 → 判定为工具查询（避免误伤长任务）。"""
    text = (prompt or "").strip()
    if not text or len(text) > 60:
        return False
    return any(k in text for k in _TIME_QUERY_KEYWORDS)


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
    state.get("messages", [])

    # 1) 快速跳过：开关关闭 / 无需编排的意图
    if not getattr(settings, "multi_agent_auto_enabled", True):
        return _skip("multi_agent disabled by config")
    if intent == "chitchat":
        return _skip("chitchat never decomposed")
    # 1.5) 时间/日期类问题快速路径：直接走动态工具循环（datetime_now），
    #      不调编排决策器 LLM —— 决策器对这类问题既慢（10s+）又易误判 MAIN_AGENT。
    if (
        intent == "query"
        and getattr(settings, "tool_loop_enabled", True)
        and _is_time_query(user_prompt)
    ):
        return {
            "decompose_decision": _tool_only_decision(
                "时间/日期类查询 → 快速路径直达动态工具循环",
            ),
            "multi_agent": False,
            "sub_agent_reports": [],
            "trace": [
                record_trace(
                    "decompose",
                    "ok",
                    mode="TOOL_ONLY",
                    reason="time_query_fast_path",
                )
            ],
        }
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

    # 1.7) 意图分析快速路径（意图识别重构 2026-08-06）：
    #      intent_analysis 已给出明确信号时直接路由，省掉编排决策器 LLM 调用。
    fast = _intent_analysis_fast_path(state)
    if fast is not None:
        return fast

    # 2) 收集决策器运行时输入（可用工具 / 权限 / 成本 / 安全策略）
    tool_specs = await _list_tools(mcp)

    # 3) 调用编排决策器；任何异常 / 缺方法 / fallback → 保守单 Agent
    decision = await _decide(state, llm, tool_specs)
    if decision is None:
        # 动态工具循环启用时：降级为 TOOL_ONLY 而不是直接 skip ——
        # skip 会让请求绕过 tool_orchestrator，连 datetime_now 这类
        # 基础工具都无法触发（responder 只能回"没有可执行的工具调用"）。
        # 循环自身对 LLM 失败有保守 FINAL_ANSWER 兜底，不会引入新风险。
        if getattr(settings, "tool_loop_enabled", True):
            return {
                "decompose_decision": _tool_only_decision(
                    "编排决策器降级 → 交给动态工具循环",
                ),
                "multi_agent": False,
                "sub_agent_reports": [],
                "trace": [
                    record_trace(
                        "decompose",
                        "skipped",
                        reason="judge fallback → route to tool loop",
                    )
                ],
            }
        return _skip("judge failed or conservative fallback")

    inner = decision.get("decision") or {}
    mode = str(inner.get("mode") or "MAIN_AGENT")
    reason = str(inner.get("reason") or "")

    # 提问策略护栏（2026-08-14）：ASK_USER 每轮最多 1 个问题，禁止 5 连问
    decision = _cap_clarifying_questions(decision)
    inner = decision.get("decision") or {}

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
            "trace": [
                record_trace(
                    "decompose",
                    "skipped",
                    reason=f"confirmation_required: {inner.get('confirmation_message') or ''}",
                )
            ],
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
    subagents = [s for s in (decision.get("selected_subagents") or []) if isinstance(s, dict)]
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
            reports.append(
                {
                    "status": "err",
                    "summary": "",
                    "error_message": f"{type(item).__name__}: {item}",
                    "confidence": 0.0,
                    "latency_ms": 0,
                }
            )
        else:
            reports.append(_serialise_report(item))

    return {
        "multi_agent": True,
        "decompose_decision": decision,
        "sub_agent_reports": reports,
        "trace": [
            record_trace(
                "decompose",
                "ok",
                mode=mode,
                sub_agents=len(specs),
                reason=reason,
            )
        ],
    }


# ---- Helpers ---------------------------------------------------------------


async def _decide(state: AgentState, llm: LMRouter, tool_specs: list[dict]) -> dict | None:
    """调用编排决策器并校验返回；失败 / fallback 返回 None（调用方走单 Agent）。"""
    from agent.graph.state import format_page_context

    try:
        decision = await llm.decompose(
            user_prompt=state.get("user_prompt", ""),
            plan=state.get("plan") or [],
            history=state.get("messages", []),
            available_subagents=AVAILABLE_SUBAGENT_CATALOG,
            available_tools=tool_specs,
            # 页面上下文（2026-08-14）：让决策器看见当前页签/场景，
            # 消除“连接”这类模糊动词的歧义
            page_context=format_page_context(state.get("page_context")),
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
    except Exception:
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
    except Exception:
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


def _ask_user_decision(question: str, missing_fields: list[str]) -> dict:
    """构造 ASK_USER 决策（追问）—— responder 会把问题输出给用户。"""
    return {
        "decision": {
            "mode": "ASK_USER",
            "should_enable_subagent": False,
            "execution_allowed": False,
            "user_confirmation_required": False,
            "confidence": 0.9,
            "reason": "意图分析判定关键信息缺失，需追问",
            "clarifying_questions": [question],
            "confirmation_message": None,
            "refusal_message": None,
        },
        "scoring": {},
        "selected_subagents": [],
        "tool_calls": [],
        "plan": [],
        "fallback": "none",
        "missing_fields": missing_fields,
    }


def _cap_clarifying_questions(decision: dict) -> dict:
    """提问策略护栏（2026-08-14）：ASK_USER 每轮最多 1 个问题。

    背景：连发多个开放式问题（如 5 连问）把认知负担全部抛给用户，
    prompt 约束失效时用代码兜底：只保留第一问，其余合并成一句提示。
    """
    inner = decision.get("decision") if isinstance(decision, dict) else None
    if not isinstance(inner, dict) or inner.get("mode") != "ASK_USER":
        return decision
    questions = [
        str(q).strip() for q in (inner.get("clarifying_questions") or []) if str(q).strip()
    ]
    if len(questions) <= 1:
        return decision
    capped = dict(decision)
    capped_inner = dict(inner)
    capped_inner["clarifying_questions"] = [
        questions[0] + "（请先回答这一条，其余细节稍后再补充。）"
    ]
    capped["decision"] = capped_inner
    return capped


def _main_agent_decision(reason: str) -> dict:
    """构造 MAIN_AGENT 决策（主智能体直接回答，不调工具）。"""
    return {
        "decision": {
            "mode": "MAIN_AGENT",
            "should_enable_subagent": False,
            "execution_allowed": True,
            "user_confirmation_required": False,
            "confidence": 0.9,
            "reason": reason,
            "clarifying_questions": [],
            "confirmation_message": None,
            "refusal_message": None,
        },
        "scoring": {},
        "selected_subagents": [],
        "tool_calls": [],
        "plan": [],
        "fallback": "none",
    }


def _intent_analysis_fast_path(state: AgentState) -> dict | None:
    """根据 intent_analysis 的明确信号直接路由（不调编排决策器 LLM）。

    优先级：追问 > need_tool=true → 工具循环 > need_tool=false → 直接回答。
    信号不明确（无分析 / 低置信度）返 None → 走既有 LLM 决策路径。
    """
    analysis = state.get("intent_analysis")
    if not isinstance(analysis, dict):
        return None
    try:
        confidence = float(analysis.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0

    # 拒绝：违法 / 越权 / 危险请求 → REFUSE（规范 §4.1）
    if analysis.get("intent_category") == "refusal" and confidence >= 0.6:
        reason = str(analysis.get("reason") or "").strip()
        message = (
            f"抱歉，该请求无法执行。{reason}"
            if reason
            else "抱歉，该请求涉及不允许的操作，无法执行。"
        )
        return {
            "decompose_decision": {
                "decision": {
                    "mode": "REFUSE",
                    "should_enable_subagent": False,
                    "execution_allowed": False,
                    "user_confirmation_required": False,
                    "confidence": confidence,
                    "reason": reason or "intent_analysis_refusal",
                    "clarifying_questions": [],
                    "confirmation_message": None,
                    "refusal_message": message,
                },
                "scoring": {},
                "selected_subagents": [],
                "tool_calls": [],
                "plan": [],
                "fallback": "none",
            },
            "multi_agent": False,
            "sub_agent_reports": [],
            "trace": [
                record_trace(
                    "decompose",
                    "ok",
                    mode="REFUSE",
                    reason="intent_analysis_refusal",
                )
            ],
        }

    # 追问：关键参数缺失且无法推断 → ASK_USER（规范 §2.4 / §14）
    if analysis.get("need_clarification") and confidence >= 0.6:
        message = str(analysis.get("clarification_message") or "").strip()
        missing = [str(f) for f in (analysis.get("missing_fields") or [])]
        if not message:
            if missing:
                message = "为了继续，请补充：" + "、".join(missing)
            else:
                message = "请补充关键信息后我再继续。"
        return {
            "decompose_decision": _ask_user_decision(message, missing),
            "multi_agent": False,
            "sub_agent_reports": [],
            "trace": [
                record_trace(
                    "decompose",
                    "ok",
                    mode="ASK_USER",
                    reason="intent_analysis_clarification",
                )
            ],
        }

    if confidence < 0.6:
        return None  # 低置信度 → 交给编排决策器 LLM 复核

    # 需要工具 → 直达动态工具循环（规范 §5.2）——明确操作不需要规划：
    # 意图分析已给出明确信号时省掉编排决策器 LLM（本地模型缺席时 30s+）。
    # 写操作照旧在工具循环内过 HITL 审批闸，红线不变。
    if analysis.get("need_tool") and getattr(settings, "tool_loop_enabled", True):
        return {
            "decompose_decision": _tool_only_decision(
                "意图分析判定需要工具 → 动态工具循环",
            ),
            "multi_agent": False,
            "sub_agent_reports": [],
            "trace": [
                record_trace(
                    "decompose",
                    "ok",
                    mode="TOOL_ONLY",
                    reason="intent_analysis_need_tool",
                )
            ],
        }

    # 不需要工具 → 主智能体直接回答（规范 §5.1）
    if analysis.get("need_tool") is False:
        return {
            "decompose_decision": _main_agent_decision(
                "意图分析判定无需工具 → 主智能体直接回答",
            ),
            "multi_agent": False,
            "sub_agent_reports": [],
            "trace": [
                record_trace(
                    "decompose",
                    "ok",
                    mode="MAIN_AGENT",
                    reason="intent_analysis_no_tool",
                )
            ],
        }
    return None


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
                sub.get("allowed_tools") if isinstance(sub.get("allowed_tools"), list) else []
            ),
            "expected_output": str(sub.get("expected_output") or ""),
            "stop_condition": str(sub.get("stop_condition") or ""),
            "safety_policy": {
                "read_only": True,
                "writes_require_hitl": True,
                "sensitive_local_only": True,
            },
        }
        specs.append(
            SubAgentSpec(
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
            )
        )
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
