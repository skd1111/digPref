"""AgentState —— LangGraph 状态机的唯一真相源。

每个节点都通过这个 TypedDict 读写。我们用 Annotated reducer，让 list 字段
在节点切换时能正确累积。"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

# ---- Literal types --------------------------------------------------------

Intent = Literal["query", "mutate", "orchestrate", "chitchat"]
ApprovalDecision = Literal["approve", "reject"]
NodeName = Literal[
    "intent",
    "planner",
    "decompose",
    "tool_orchestrator",
    "tool_runner",
    "hitl_gate",
    "repair",
    "responder",
    # Phase 4 V0
    "vision_understand",
    # Phase 4 V1
    "local_intent",
    "rag_retrieve",
    # Phase 18 双框架
    "mode_router",
]
StepStatus = Literal["running", "ok", "fail", "skipped"]


# ---- AgentState ------------------------------------------------------------


class AgentState(TypedDict, total=False):
    """LangGraph state — every node reads & returns a partial update."""

    # ---- Conversation ----
    messages: Annotated[list, add_messages]
    # ^ messages is auto-merged: langgraph's add_messages reducer handles
    #   both new BaseMessage and {role, content} dict formats.
    run_id: str | None  # 本次运行的 thread/run id（子 Agent 关联用）

    # ---- User intent & planning ----
    user_prompt: str  # the original user message
    intent: Intent | None
    # 意图识别重构（2026-08-06）：结构化意图分析（IntentAnalysis.to_dict）
    intent_analysis: dict | None  # 含 need_tool / need_clarification / risk_level 等
    rewritten_query: str | None  # 结合上下文改写后的完整请求
    plan: list[dict]  # ordered tool calls
    plan_explanation: str | None  # why this plan

    # ---- Phase 12 V2 自动多智能体（Agent 自动判断，非用户手动选择）----
    multi_agent: bool  # 本次运行是否使用了多智能体
    decompose_decision: Any | None  # 自动分解判定结果（含 reason / sub_tasks）
    sub_agent_reports: Annotated[list[dict], add]  # 子 Agent 回报（自动派生后汇总）

    # ---- 动态工具加载与调用（2026-08-03）----
    load_stage: str  # SUMMARY_ONLY | CANDIDATE_REGISTERED | FULL_REGISTERED
    full_toolset_loaded: bool  # 是否已全量注册工具
    registered_tools: list[dict]  # 已注册工具的完整定义（可调用）
    tool_results: list[dict]  # 工具执行结果（循环累积）
    tool_turn_count: int  # 动态工具循环轮次
    tool_loop_active: bool  # 循环内 HITL 审批后路由回循环的标志
    # 停滞熔断（2026-08-25）：连续零成功执行的轮数（prompt 模式跨图轮累计，
    # native 内层循环自带局部计数）；达阈提前终止，防小模型死循环空转
    tool_stagnant_streak: int
    # 重复调用熔断跨图轮状态（BUGFIX #165）：上一次调用指纹 + 连续重复次数。
    # prompt 模式每轮是独立节点执行，不存 state 就无法识别跨轮的原地打转。
    tool_last_call_fp: str
    tool_repeat_streak: int
    # OpenAI 原生工具调用模式（2026-08-07）：每次运行首节点探测一次后固定
    tool_calling_mode: str  # "prompt"（提示词协议，默认）| "native"
    native_backend: str | None  # native 模式实际后端（private / cloud）
    native_turn_context: dict | None  # native 循环消息上下文 + HITL 暂停时未执行的剩余调用

    # ---- Loop bookkeeping ----
    current_step_index: int  # index into plan[]
    pending_tool_call: dict | None
    tool_result: Any | None
    tool_error: str | None
    retry_count: int  # total repair attempts across this run

    # ---- HITL ----
    approval_id: str | None
    approval_decision: ApprovalDecision | None
    approval_decided_at: str | None
    approval_started_at: float | None  # 审批发起时间戳（gate 侧超时守卫，fail-closed）
    awaiting_approval: bool  # signal to SSE adapter
    approval_options: dict | None  # Phase 18：推荐选项（options/recommended/reason）

    # ---- Trace ----
    trace: Annotated[list[dict], add]  # append-only trace events
    step_started_at: str | None

    # ---- Final ----
    final_answer: str | None
    sources: list[str]  # tool results referenced
    truncated_any: bool

    # ---- Phase 2D V0 Skill 路由 ----
    active_skill_id: str | None
    active_skill_name: str | None
    skill_routing: Any | None  # SkillRoutingResult (avoid import cycle)
    # Skill 粘性（2026-08-26）：前端记录上一轮命中的 skill（skill_matched 事件），
    # 本轮未命中新 skill 且属追问/修改类输入时继承，防「太丑了重做」裸生成。
    last_skill_id: str | None

    # ---- 任务级工作目录（2026-08-26）----
    # 一个聊天页签 = 一个任务文件夹：task_id = 前端页签唯一标识（映射持久化），
    # task_title = 首个问题摘要（任务文件夹命名用）。
    task_id: str | None
    task_title: str | None

    # ---- Phase 4 V0 本地端侧模型 ----
    inference_mode: str  # "normal" | "performance"（默认 normal）
    screenshot: str | None  # base64 编码截图
    vision_question: str | None  # 截图理解问题（可选）
    vision_result: str | None  # 视觉模型输出文字

    # ---- Phase 4 V1 RAG 检索增强 ----
    rag_context: Any | None  # RAGContext 实例（含 results + formatted_prompt）
    system_prompt_addon: str  # 拼入 system prompt 的片段（来自 RAG）

    # ---- Phase 18 双框架（Coding Agent vs Work Agent）----
    work_mode: str  # "full"|"operator"|"auditor"|"analyst"（前端 WorkMode 透传）
    autonomy: str  # "interactive"|"auto"（会话级自主性）
    # 页面上下文（2026-08-14）：前端当前页签/场景（如 {page: {workMode, tabTitle}}），
    # 注入 intent / decompose prompt，消除“连接”这类模糊动词的场景歧义
    page_context: dict[str, Any] | None
    routing: str | None  # "coding"|"work"|"mixed"（ModeRouter 判定）
    routing_overridden: bool  # 路由结果偏离当前模式默认
    routing_declaration: str | None  # 偏离声明文案（responder 引用）
    execution_policies: list[dict]  # 子任务级 ExecutionPolicy（含 framework 标签）
    error_feedback: list[dict]  # Auto-Repair 反馈栈（attempt/error/files）
    repair_attempt: int  # 当前 repair 次数
    needs_human_intervention: bool  # repair 达上限后移交人工
    dual_rules_addon: str  # Code/Work 双模式执行纪律（注入工具循环 prompt）


# ---- Helpers ---------------------------------------------------------------


def empty_state(prompt: str) -> dict:
    """返回新运行的初始状态（所有字段均为默认值）。

    使用 datetime.timezone.utc 而非 datetime.UTC，兼容 Python 3.10+。
    """
    from datetime import datetime, timezone

    return {
        "messages": [{"role": "user", "content": prompt}],
        "run_id": None,
        "user_prompt": prompt,
        "intent": None,
        "intent_analysis": None,
        "rewritten_query": None,
        "plan": [],
        "plan_explanation": None,
        "multi_agent": False,
        "decompose_decision": None,
        "sub_agent_reports": [],
        "load_stage": "SUMMARY_ONLY",
        "full_toolset_loaded": False,
        "registered_tools": [],
        "tool_results": [],
        "tool_turn_count": 0,
        "tool_loop_active": False,
        "tool_stagnant_streak": 0,
        "tool_last_call_fp": "",
        "tool_repeat_streak": 0,
        "tool_calling_mode": "prompt",
        "native_backend": None,
        "native_turn_context": None,
        "current_step_index": 0,
        "pending_tool_call": None,
        "tool_result": None,
        "tool_error": None,
        "retry_count": 0,
        "approval_id": None,
        "approval_decision": None,
        "approval_decided_at": None,
        "approval_started_at": None,
        "awaiting_approval": False,
        "approval_options": None,
        "trace": [],
        "step_started_at": datetime.now(timezone.utc).isoformat(),
        "final_answer": None,
        "sources": [],
        "truncated_any": False,
        # Phase 4 V0
        "inference_mode": "normal",
        "screenshot": None,
        "vision_question": None,
        "vision_result": None,
        # Phase 4 V1
        "rag_context": None,
        "system_prompt_addon": "",
        # Phase 18 双框架
        "work_mode": "full",
        "autonomy": "interactive",
        "page_context": None,
        "routing": None,
        "routing_overridden": False,
        "routing_declaration": None,
        "execution_policies": [],
        "error_feedback": [],
        "repair_attempt": 0,
        "needs_human_intervention": False,
        "dual_rules_addon": "",
        # Skill 粘性 + 任务级工作目录（2026-08-26）
        "last_skill_id": None,
        "task_id": None,
        "task_title": None,
    }


def next_step(state: AgentState) -> dict | None:
    """Return the next pending tool call in the plan, or None if done."""
    plan = state.get("plan") or []
    idx = state.get("current_step_index", 0)
    if idx >= len(plan):
        return None
    return plan[idx]


def advance(state: AgentState) -> dict:
    return {"current_step_index": state.get("current_step_index", 0) + 1}


def format_page_context(page_context: Any) -> str:
    """把前端传来的页面上下文 dict 压成一行人话（注入 intent / decompose prompt）。

    输入形如 {"page": {"workMode": "operator", "tabTitle": "内网模型接入配置"}}；
    非法/空输入返空串（调用方判空后决定是否注入）。
    """
    if not isinstance(page_context, dict):
        return ""
    page = page_context.get("page")
    if not isinstance(page, dict):
        page = page_context
    tab_title = str(page.get("tabTitle") or "").strip()
    work_mode = str(page.get("workMode") or "").strip()
    route = str(page.get("route") or "").strip()
    parts: list[str] = []
    if tab_title:
        parts.append(f"当前页签「{tab_title[:60]}」")
    if route:
        parts.append(f"页面路由 {route[:60]}")
    if work_mode:
        parts.append(f"模式 {work_mode}")
    return "、".join(parts)


def record_trace(node: NodeName, status: StepStatus, **meta: Any) -> dict:
    """构建一条 trace 记录 —— 节点返回此字典作为状态更新的一部分。"""
    from datetime import datetime, timezone

    entry: dict[str, Any] = {
        "node": node,
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    entry.update(meta)
    return entry
