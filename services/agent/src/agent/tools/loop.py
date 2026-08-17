"""DynamicToolLoop —— 动态工具加载与调用循环（系统侧执行逻辑）。

模型每次返回一个动作（用户定稿协议）：
    - SELECT_TOOLS       → 只注册 selected_tool_names → 再次调用模型
    - TOOL_CALLS         → 执行 tool_calls → 结果追加上下文 → 再次调用模型
    - REQUEST_FULL_TOOLS → 全量注册 → FULL_TOOLSET_LOADED=true → 再次调用模型
    - ASK_USER           → 把 ask_user_message 返回给用户
    - FINAL_ANSWER       → 把 final_answer 返回给用户

安全不变量：只调用已注册工具；全量后不得再请求全量；写 / 高危调用暂停等 HITL
审批；轮次硬上限防死循环；解析失败 / 违规动作一律保守回退 FINAL_ANSWER。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.config import settings
from agent.dual.repair import validate_written_files  # Phase 18 Auto-Repair
from agent.graph.state import record_trace
from agent.llm.json_discipline import strip_think_blocks
from agent.llm.prompts import current_time_text
from agent.llm.router import LMRouter

logger = logging.getLogger("agent.tools.loop")


def _decision_hint(decision: Any) -> str:
    """把 decompose 决策压成交接提示（工具循环重建任务上下文用）。

    背景（BUGFIX #108）：用户在确认卡点「确认执行」后，新一轮的 user_prompt
    只剩一句确认文本，工具循环模型重建不出上一轮谈好的参数 → 直接 FINAL_ANSWER
    放弃。把 decompose 已判定的模式 / 理由 / 建议工具调用 / 确认文案交给循环，
    让它照着已确认的方案继续。非 TOOL_ONLY / 无信息时返空串（不注入）。
    """
    inner = decision.get("decision") if isinstance(decision, dict) else None
    if not isinstance(inner, dict):
        return ""
    mode = str(inner.get("mode") or "")
    if mode != "TOOL_ONLY":
        return ""
    parts: list[str] = []
    reason = str(inner.get("reason") or "").strip()
    if reason:
        parts.append(f"决策理由：{reason[:300]}")
    confirmation = str(inner.get("confirmation_message") or "").strip()
    if confirmation:
        parts.append(f"已向用户出示并获确认的参数方案：{confirmation[:800]}")
    # 决策 JSON 里 tool_calls 在顶层（与 decision 平级），兼容内层写法
    calls = decision.get("tool_calls") if isinstance(decision, dict) else None
    if not isinstance(calls, list):
        inner_calls = inner.get("tool_calls")
        calls = inner_calls if isinstance(inner_calls, list) else None
    if calls:
        slim: list[dict[str, Any]] = []
        for c in calls[:5]:
            if not isinstance(c, dict):
                continue
            slim.append(
                {
                    "tool": str(c.get("tool") or c.get("name") or ""),
                    "purpose": str(c.get("purpose") or "")[:200],
                    "inputs": c.get("inputs") if isinstance(c.get("inputs"), dict) else {},
                }
            )
        if slim:
            parts.append("建议的工具调用：" + json.dumps(slim, ensure_ascii=False)[:1000])
    return "\n".join(parts)[:2000]


def _brief_value(value: Any, limit: int = 60) -> str:
    """把工具参数压成短展示文本（思维链打印用，防大参数刷屏）。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _tool_op_trace(name: str, args: dict, result: dict) -> dict:
    """生成一条 per-tool trace —— 把每个工具操作打印进思维链（2026-08-17）。

    此前工具循环一次节点执行只留一条聚合 trace（calls=N），read / write /
    glob / grep 等具体操作在思维链里不可见。每工具一条带 summary 的条目，
    前端思维链面板 / 持久化思维链都能直接渲染。
    """
    ok = bool(result.get("ok"))
    pairs = [f"{k}={_brief_value(v)}" for k, v in list(args.items())[:3]]
    if len(args) > 3:
        pairs.append("…")
    arg_brief = ", ".join(pairs)
    if ok:
        summary = f"调用工具 {name}({arg_brief}) → 成功"
    else:
        err = str(result.get("error") or "未知错误")[:120]
        summary = f"调用工具 {name}({arg_brief}) → 失败：{err}"
    return record_trace(
        "tool_orchestrator",
        "ok" if ok else "fail",
        action="TOOL_CALL",
        tool=name,
        summary=summary,
    )


# 原生模式首轮默认可用的确定性工具（时间类 + 追问伪工具）
_NATIVE_FIRST_ROUND_TOOLS = ("datetime_now", "date_parse")

_NATIVE_SYSTEM_PROMPT = (
    "你是企业内网 AI IDE 的工具执行助手，通过原生工具调用完成任务。\n"
    "纪律：\n"
    "1. 绝不编造工具未返回的数据；工具没返回的信息一律说「未查询到」。\n"
    "2. 相对时间（明天/下周一/最近三天）必须先调 date_parse 转成绝对日期再用于其他工具。\n"
    "3. 时间敏感问题（今天几号/农历/星期几）直接调 datetime_now。\n"
    "4. 缺少关键信息时用 ask_user 追问，一次只问最关键的问题并给示例。\n"
    "5. 写/高危操作照常发起调用，系统会自动拦截进入人工审批，不要自行拒绝。\n"
    "6. 任务完成时直接输出面向用户的自然语言回答，不再调用工具。\n"
    "7. 当前日期/时间/星期只能以系统注入的当前时间或 datetime_now 返回为准；"
    "你对「今天」没有可靠感知，严禁凭记忆回答日期（会编造）。\n"
    "8. 回答必须使用与用户输入一致的语言；用户用中文提问时一律用中文作答，"
    "禁止整段英文输出（代码块、数字、标识符与专有名词保持原样，不翻译）。"
)

_ASK_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": "缺少关键信息时向用户追问（一次只问最关键的问题，并给出示例）",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "面向用户的追问内容"},
            },
            "required": ["message"],
        },
    },
}


class DynamicToolLoop:
    """一次「工具编排循环」的驱动（一个图节点 = 一轮循环直到出结果）。"""

    def __init__(
        self,
        llm: LMRouter,
        catalog: Any,
        *,
        max_turns: int | None = None,
        max_selected: int | None = None,
        max_result_chars: int | None = None,
        max_results_kept: int | None = None,
    ) -> None:
        self._llm = llm
        self._catalog = catalog
        self._max_turns = max_turns or settings.tool_loop_max_turns
        self._max_selected = max_selected or settings.tool_loop_max_selected
        self._max_result_chars = max_result_chars or settings.tool_loop_max_result_chars
        self._max_results_kept = max_results_kept or settings.tool_loop_max_results_kept

    async def run(self, state: dict) -> dict:
        """运行一轮循环（可能包含 HITL 暂停/恢复），返回 AgentState 增量。

        2026-08-07：tool_calling_mode="native" 时走 OpenAI 原生 function calling
        循环；探测不可用 / 后端故障时自动回退提示词协议。
        """
        if str(state.get("tool_calling_mode") or "prompt") == "native":
            native = await self._run_native(state)
            if native is not None:
                return native
            # None → 后端不可用，落入下方提示词协议
        updates: dict = {}
        # 审批决定已到达（awaiting 已被 hitl_gate 清除）或仍带审批态 → 先恢复
        if state.get("approval_decision") or state.get("awaiting_approval"):
            updates = await self._resume_approval(state)
            if updates.get("awaiting_approval"):
                return updates  # 仍在等待（异常路径，保守保持暂停）

        merged = {**state, **updates}
        user_input = str(merged.get("user_prompt") or "")
        messages = merged.get("messages") or []
        tool_results = list(merged.get("tool_results") or [])
        registered = list(merged.get("registered_tools") or [])
        full_loaded = bool(merged.get("full_toolset_loaded"))
        load_stage = str(merged.get("load_stage") or "SUMMARY_ONLY")
        turn = int(merged.get("tool_turn_count") or 0) + 1
        # 编排决策交接（2026-08-17，BUGFIX #108）：decompose 已判定的工具/参数
        # （含用户刚确认的方案）随 prompt 交给循环，避免确认后循环丢失上下文

        if turn > self._max_turns:
            return {
                **updates,
                **self._done(
                    final_answer="已达到工具调用轮次上限，已停止继续尝试。请缩小问题范围或换一种说法。",
                    tool_turn_count=turn,
                    tool_results=tool_results,
                    trace=[record_trace("tool_orchestrator", "fail", reason="max_turns")],
                ),
            }

        try:
            action = await self._llm.orchestrate_tools(
                load_stage=load_stage,
                user_input=user_input,
                messages=messages,
                tool_summaries=await self._catalog.summaries(),
                registered_tools=registered,
                full_toolset_loaded=full_loaded,
                tool_results=self._format_results(tool_results),
                max_selected_tools=self._max_selected,
                decision_hint=_decision_hint(merged.get("decompose_decision")),
                # Phase 18：Code/Work 双模式执行纪律（mode_router 注入）
                extra_rules=str(merged.get("dual_rules_addon") or ""),
                # 运行时上下文：工作模式 / 自主级别 / 任务路由（提示词 4.10–4.12）
                work_mode=str(merged.get("work_mode") or ""),
                autonomy=str(merged.get("autonomy") or ""),
                routing=str(merged.get("routing") or ""),
            )
        except Exception as exc:
            return {
                **updates,
                **self._done(
                    final_answer=f"工具编排失败（{type(exc).__name__}），已停止尝试。",
                    tool_turn_count=turn,
                    tool_results=tool_results,
                    trace=[record_trace("tool_orchestrator", "fail", error=str(exc))],
                ),
            }

        if not isinstance(action, dict) or action.get("_fallback"):
            return {
                **updates,
                **self._done(
                    final_answer="抱歉，我暂时无法完成这个任务（工具编排不可用）。",
                    tool_turn_count=turn,
                    tool_results=tool_results,
                    trace=[record_trace("tool_orchestrator", "skipped", reason="fallback")],
                ),
            }

        kind = action.get("action")
        if kind == "SELECT_TOOLS":
            names = [str(n) for n in (action.get("selected_tool_names") or [])]
            defs = await self._catalog.definitions(names)
            if not defs:
                return {
                    **updates,
                    **self._done(
                        final_answer="当前没有可用的候选工具来完成这个任务。",
                        tool_turn_count=turn,
                        tool_results=tool_results,
                        trace=[record_trace("tool_orchestrator", "fail", reason="no_candidates")],
                    ),
                }
            return {
                **updates,
                **self._continue(
                    load_stage="CANDIDATE_REGISTERED",
                    registered_tools=defs,
                    tool_turn_count=turn,
                    tool_results=tool_results,
                    trace=[
                        record_trace(
                            "tool_orchestrator",
                            "ok",
                            action="SELECT_TOOLS",
                            selected=len(defs),
                        )
                    ],
                ),
            }

        if kind == "REQUEST_FULL_TOOLS":
            defs = await self._catalog.definitions()
            return {
                **updates,
                **self._continue(
                    load_stage="FULL_REGISTERED",
                    full_toolset_loaded=True,
                    registered_tools=defs,
                    tool_turn_count=turn,
                    tool_results=tool_results,
                    trace=[
                        record_trace(
                            "tool_orchestrator",
                            "ok",
                            action="REQUEST_FULL_TOOLS",
                            registered=len(defs),
                        )
                    ],
                ),
            }

        if kind == "TOOL_CALLS":
            registered_names = {str(t.get("name")) for t in registered}
            executed: list[dict] = []
            executed_pairs: list[tuple[dict, dict]] = []  # Phase 18：供 Auto-Repair 钩子
            op_traces: list[dict] = []  # 逐工具思维链条目（2026-08-17）
            for call in action.get("tool_calls") or []:
                name = str(call.get("name") or "")
                call_args = dict(call.get("arguments") or {})
                if name not in registered_names:
                    executed.append(
                        {
                            "id": call.get("id"),
                            "name": name,
                            "ok": False,
                            "error": "unregistered_tool",
                        }
                    )
                    op_traces.append(_tool_op_trace(name, call_args, executed[-1]))
                    continue
                result = await self._catalog.execute(
                    name,
                    call_args,
                    merged,
                )
                if result.get("awaiting_approval"):
                    return {
                        **updates,
                        **self._pause(
                            result["pending_tool_call"],
                            tool_turn_count=turn,
                            tool_results=tool_results,
                        ),
                    }
                executed.append(result)
                executed_pairs.append((call, result))
                op_traces.append(_tool_op_trace(name, call_args, result))
            tool_results = (tool_results + executed)[-self._max_results_kept :]

            # Phase 18 Auto-Repair：coding 子任务写文件后确定性验证
            repair = validate_written_files(merged, executed_pairs)
            repair_update: dict = {}
            if repair:
                tool_results = (tool_results + repair["extra_results"])[-self._max_results_kept :]
                repair_update = {
                    "error_feedback": repair["error_feedback"],
                    "repair_attempt": repair["repair_attempt"],
                }
                if repair.get("needs_human_intervention"):
                    return {
                        **updates,
                        **repair_update,
                        "needs_human_intervention": True,
                        "tool_loop_active": False,
                        "tool_turn_count": turn,
                        "tool_results": tool_results,
                        "final_answer": (
                            "Auto-Repair 已达修复上限，代码仍未通过验证，已停止自动重试。"
                            "请人工检查错误详情后给出新指令。"
                        ),
                        "trace": repair["trace"]
                        + [
                            record_trace(
                                "tool_orchestrator",
                                "fail",
                                reason="repair_exhausted",
                            )
                        ],
                    }
            return {
                **updates,
                **repair_update,
                **self._continue(
                    tool_results=tool_results,
                    tool_turn_count=turn,
                    trace=op_traces
                    + [
                        record_trace(
                            "tool_orchestrator",
                            "ok",
                            action="TOOL_CALLS",
                            calls=len(executed),
                        )
                    ]
                    + (repair["trace"] if repair else []),
                ),
            }

        if kind == "ASK_USER":
            ask_message = strip_think_blocks(str(action.get("ask_user_message") or "")).strip()
            return {
                **updates,
                **self._done(
                    final_answer=ask_message or "需要补充信息后才能继续。",
                    tool_turn_count=turn,
                    tool_results=tool_results,
                    trace=[record_trace("tool_orchestrator", "ok", action="ASK_USER")],
                ),
            }

        # FINAL_ANSWER
        # think 剥离（2026-08-17，BUGFIX #108）：推理模型会把内心独白塞进
        # final_answer，直接透传会把 <think> 原文暴露给用户
        answer = strip_think_blocks(str(action.get("final_answer") or "")).strip()
        if not answer.strip():
            if tool_results:
                return {
                    **updates,
                    **self._done(
                        final_answer=None,
                        tool_turn_count=turn,
                        tool_results=tool_results,
                        trace=[record_trace("tool_orchestrator", "ok", action="FINAL_ANSWER")],
                    ),
                }
            answer = "抱歉，我暂时无法完成这个任务。"
        return {
            **updates,
            **self._done(
                final_answer=answer,
                tool_turn_count=turn,
                tool_results=tool_results,
                trace=[record_trace("tool_orchestrator", "ok", action="FINAL_ANSWER")],
            ),
        }

    # ---- OpenAI 原生 function calling 循环（2026-08-07）------------------

    async def _run_native(self, state: dict) -> dict | None:
        """原生工具调用循环；后端不可用返 None → 调用方回退提示词协议。

        HITL / Auto-Repair / 轮次上限语义与提示词协议完全一致；
        写/高危调用照样暂停交 hitl_gate 审批。
        """
        resolved = None
        if hasattr(self._llm, "resolve_native_backend"):
            try:
                resolved = await self._llm.resolve_native_backend()
            except Exception:  # 探测故障 → 回退提示词协议
                resolved = None
        if not resolved:
            return None
        backend_name, backend = resolved

        updates: dict = {}
        resuming = bool(state.get("approval_decision") or state.get("awaiting_approval"))
        if resuming:
            updates = {
                "awaiting_approval": False,
                "approval_id": None,
                "approval_decision": None,
                "pending_tool_call": None,
                "plan": [],
                "current_step_index": 0,
                "tool_loop_active": True,
                "tool_turn_count": int(state.get("tool_turn_count") or 0),
            }
        merged = {**state, **updates}
        tool_results = list(merged.get("tool_results") or [])
        turn = int(merged.get("tool_turn_count") or 0) + 1
        if turn > self._max_turns:
            return {
                **updates,
                **self._done(
                    final_answer="已达到工具调用轮次上限，已停止继续尝试。请缩小问题范围或换一种说法。",
                    tool_turn_count=turn,
                    tool_results=tool_results,
                    trace=[
                        record_trace("tool_orchestrator", "fail", reason="max_turns", mode="native")
                    ],
                ),
            }

        ctx = merged.get("native_turn_context") or {}
        messages = list(ctx.get("messages") or [])
        pending_calls = list(ctx.get("pending_calls") or [])
        full_loaded = bool(merged.get("full_toolset_loaded"))
        op_traces: list[dict] = []  # 逐工具思维链条目（2026-08-17）

        def _emit(done: dict) -> dict:
            """统一出口：带上本轮全量加载状态 + 逐工具操作条目。"""
            if op_traces:
                done = {**done, "trace": op_traces + list(done.get("trace") or [])}
            return {**updates, "full_toolset_loaded": full_loaded, **done}

        if not messages:
            system = _NATIVE_SYSTEM_PROMPT
            addon = str(merged.get("dual_rules_addon") or "")
            if addon:
                system += "\n\n" + addon
            # 当前时间注入（BUGFIX #113）：native 循环的 FINAL_ANSWER 由模型
            # 直接透传给用户不经 summarise，不注入时间基准时会凭记忆编造日期
            # （用户问「今天几号」答「10月10日」）。纪律见 _NATIVE_SYSTEM_PROMPT §7。
            system += f"\n\n【当前时间（系统本地，唯一可信基准）】\n{current_time_text()}"
            messages = [{"role": "system", "content": system}]
            for h in (merged.get("messages") or [])[-4:]:
                role = getattr(h, "role", None) or (h.get("role") if isinstance(h, dict) else None)
                content = getattr(h, "content", None) or (
                    h.get("content") if isinstance(h, dict) else None
                )
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": str(content)})
            # 编排决策交接（BUGFIX #108）：确认后新轮次的 user_prompt 只有一句
            # 确认文本，把 decompose 已判定的方案拼进去，避免循环丢失上下文
            hint = _decision_hint(merged.get("decompose_decision"))
            native_user_input = str(merged.get("user_prompt") or "")
            if hint:
                native_user_input = f"[编排决策交接]\n{hint}\n\n[用户当前输入]\n{native_user_input}"
            messages.append({"role": "user", "content": native_user_input})
        elif resuming:
            # HITL 恢复：批准的调用放回待执行队首；拒绝的记入结果
            pending = state.get("pending_tool_call")
            decision = state.get("approval_decision")
            if decision == "approve" and pending:
                pending_calls = [
                    {
                        "id": str(pending.get("call_id") or "pending"),
                        "name": str(pending.get("name") or ""),
                        "arguments": dict(pending.get("args") or {}),
                    },
                    *pending_calls,
                ]
            elif decision == "reject" and pending:
                tool_results = (
                    [
                        *tool_results,
                        {
                            "id": pending.get("call_id"),
                            "name": pending.get("name"),
                            "ok": False,
                            "error": "user_rejected",
                        },
                    ]
                )[-self._max_results_kept :]
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(pending.get("call_id") or ""),
                        "content": "用户已拒绝执行该操作。",
                    }
                )

        name_map: dict[str, str] = {}  # 清洗后函数名 → 真实工具名
        # HITL 恢复时先执行审批后剩余的调用，再回到模型轮
        calls_to_execute: list[dict] | None = pending_calls if pending_calls else None
        # 批准重放批次：显式注入 approve，避免 dispatcher 二次拦截
        batch_approved = resuming and state.get("approval_decision") == "approve"
        for _ in range(self._max_turns):
            defs = await self._native_tool_defs(full_loaded, name_map)
            # 本轮允许调用的真实工具名（含 MCP）+ 伪工具
            callable_names = (
                set(name_map.values())
                | {"ask_user"}
                | (set() if full_loaded else {"use_more_tools"})
            )
            if calls_to_execute is None:
                try:
                    resp = await backend.chat_with_tools(messages, defs)
                except Exception as exc:  # 后端故障：未执行过工具则回退提示词协议
                    logger.warning("native tool calling failed (backend=%s): %s", backend_name, exc)
                    if not tool_results and not resuming:
                        return None
                    return _emit(
                        self._done(
                            final_answer=f"工具编排失败（{type(exc).__name__}），已停止尝试。",
                            tool_turn_count=turn,
                            tool_results=tool_results,
                            trace=[
                                record_trace(
                                    "tool_orchestrator", "fail", error=str(exc), mode="native"
                                )
                            ],
                        )
                    )

                resp_calls = resp.get("tool_calls") or []
                if not resp_calls:
                    # think 剥离（BUGFIX #108）：推理模型的内心独白不得透传给用户
                    answer = strip_think_blocks(str(resp.get("content") or "")).strip()
                    return _emit(
                        self._done(
                            final_answer=answer or None,
                            tool_turn_count=turn,
                            tool_results=tool_results,
                            trace=[
                                record_trace(
                                    "tool_orchestrator",
                                    "ok",
                                    action="FINAL_ANSWER",
                                    mode="native",
                                )
                            ],
                        )
                    )

                # assistant 消息带 tool_calls（协议要求；函数名与注册时一致）
                messages.append(
                    {
                        "role": "assistant",
                        "content": resp.get("content") or "",
                        "tool_calls": [
                            {
                                "id": c["id"],
                                "type": "function",
                                "function": {
                                    "name": c["name"],
                                    "arguments": json.dumps(
                                        c.get("arguments") or {},
                                        ensure_ascii=False,
                                    ),
                                },
                            }
                            for c in resp_calls
                        ],
                    }
                )
                calls_to_execute = [
                    {"id": c["id"], "name": c["name"], "arguments": c.get("arguments") or {}}
                    for c in resp_calls
                ]

            calls = calls_to_execute
            calls_to_execute = None
            executed: list[dict] = []
            executed_pairs: list[tuple[dict, dict]] = []
            for i, call in enumerate(calls):
                real_name = name_map.get(self._sanitize_tool_name(call["name"], {}), call["name"])
                if real_name == "ask_user":
                    msg = str(
                        (call.get("arguments") or {}).get("message") or "需要补充信息后才能继续。"
                    )
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": msg})
                    done = self._done(
                        final_answer=msg,
                        tool_turn_count=turn,
                        tool_results=tool_results,
                        trace=[
                            record_trace(
                                "tool_orchestrator", "ok", action="ASK_USER", mode="native"
                            )
                        ],
                    )
                    done["native_turn_context"] = {"messages": messages}
                    return _emit(done)
                if real_name == "use_more_tools" and not full_loaded:
                    full_loaded = True
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": "已加载全量工具，请继续完成任务。",
                        }
                    )
                    continue
                if real_name not in callable_names and not batch_approved:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": "unregistered_tool：该工具未注册；如需更多工具请先调用 use_more_tools。",
                        }
                    )
                    continue
                result = await self._catalog.execute(
                    real_name,
                    dict(call.get("arguments") or {}),
                    {**merged, "approval_decision": "approve"} if batch_approved else merged,
                )
                if result.get("awaiting_approval"):
                    # HITL 暂停：剩余未执行的调用存入上下文，审批后继续
                    remaining = [
                        {"id": c["id"], "name": c["name"], "arguments": c.get("arguments") or {}}
                        for c in calls[i + 1 :]
                    ]
                    pause = self._pause(
                        result["pending_tool_call"],
                        tool_turn_count=turn,
                        tool_results=tool_results,
                    )
                    pause["native_turn_context"] = {
                        "messages": messages,
                        "pending_calls": remaining,
                    }
                    return _emit(pause)
                executed.append(result)
                executed_pairs.append(
                    (
                        {"name": real_name, "arguments": call.get("arguments") or {}},
                        result,
                    )
                )
                op_traces.append(
                    _tool_op_trace(real_name, dict(call.get("arguments") or {}), result)
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(
                            {
                                "ok": result.get("ok"),
                                "error": result.get("error"),
                                "result": result.get("result"),
                            },
                            ensure_ascii=False,
                            default=str,
                        )[: self._max_result_chars],
                    }
                )
            tool_results = (tool_results + executed)[-self._max_results_kept :]
            batch_approved = False  # 仅重放批次携带 approve，后续模型轮恢复正常闸门

            # Phase 18 Auto-Repair：coding 子任务写文件后确定性验证
            repair = validate_written_files(merged, executed_pairs)
            repair_update: dict = {}
            if repair:
                tool_results = (tool_results + repair["extra_results"])[-self._max_results_kept :]
                repair_update = {
                    "error_feedback": repair["error_feedback"],
                    "repair_attempt": repair["repair_attempt"],
                }
                if repair.get("needs_human_intervention"):
                    return _emit(
                        {
                            **repair_update,
                            "needs_human_intervention": True,
                            "tool_loop_active": False,
                            "tool_turn_count": turn,
                            "tool_results": tool_results,
                            "final_answer": (
                                "Auto-Repair 已达修复上限，代码仍未通过验证，已停止自动重试。"
                                "请人工检查错误详情后给出新指令。"
                            ),
                            "trace": repair["trace"]
                            + [
                                record_trace(
                                    "tool_orchestrator",
                                    "fail",
                                    reason="repair_exhausted",
                                    mode="native",
                                )
                            ],
                        }
                    )
            # 继续循环：把工具结果交给模型判断下一步

        return _emit(
            self._done(
                final_answer="已达到工具调用轮次上限，已停止继续尝试。请缩小问题范围或换一种说法。",
                tool_turn_count=turn,
                tool_results=tool_results,
                trace=[
                    record_trace("tool_orchestrator", "fail", reason="max_turns", mode="native")
                ],
            )
        )

    # ---- 原生模式辅助 ------------------------------------------------------

    @staticmethod
    def _sanitize_tool_name(name: str, name_map: dict[str, str]) -> str:
        """清洗函数名为 OpenAI 合法字符集（a-z A-Z 0-9 _ -），并登记映射。"""
        import re as _re

        sanitized = _re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64] or "tool"
        name_map[sanitized] = name
        return sanitized

    async def _native_tool_defs(
        self,
        full_loaded: bool,
        name_map: dict[str, str],
    ) -> list[dict]:
        """按阶段构造 OpenAI tools 参数（首轮轻量 → 全量）。"""
        tools: list[dict] = [_ASK_USER_TOOL]
        if not full_loaded:
            defs = await self._catalog.definitions(list(_NATIVE_FIRST_ROUND_TOOLS))
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "use_more_tools",
                        "description": "当前工具不足以完成任务时调用，系统将加载全量工具集",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            )
        else:
            defs = await self._catalog.definitions()
        for d in defs:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": self._sanitize_tool_name(str(d.get("name") or ""), name_map),
                        "description": str(d.get("description") or ""),
                        "parameters": d.get("parameters") or {"type": "object", "properties": {}},
                    },
                }
            )
        return tools

    # ---- HITL 暂停 / 恢复 -------------------------------------------------

    async def _resume_approval(self, state: dict) -> dict:
        """审批决定后回到循环：批准 → 重放该调用；拒绝 → 记入结果再继续。"""
        pending = state.get("pending_tool_call")
        tool_results = list(state.get("tool_results") or [])
        base = {
            "awaiting_approval": False,
            "approval_id": None,
            "approval_decision": None,
            "pending_tool_call": None,
            "plan": [],
            "current_step_index": 0,
            "tool_loop_active": True,
            "tool_turn_count": int(state.get("tool_turn_count") or 0),
        }
        decision = state.get("approval_decision")
        if not decision and state.get("awaiting_approval"):
            # 仍在等待（不应到达本节点；防御性保持暂停）
            return {
                **base,
                "awaiting_approval": True,
                "pending_tool_call": pending,
                "plan": [pending] if pending else [],
            }
        if decision == "approve" and pending:
            result = await self._catalog.execute(
                str(pending.get("name") or ""),
                dict(pending.get("args") or {}),
                {**state, "approval_decision": "approve"},
            )
            if result.get("awaiting_approval"):
                # 不应发生；保守保持暂停
                return {
                    **base,
                    "awaiting_approval": True,
                    "pending_tool_call": pending,
                    "plan": [pending],
                }
            tool_results = ([*tool_results, result])[-self._max_results_kept :]
            # Phase 18 Auto-Repair：审批后执行的写操作同样要验证
            repair = validate_written_files(state, [(pending, result)])
            if repair:
                tool_results = (tool_results + repair["extra_results"])[-self._max_results_kept :]
                base["error_feedback"] = repair["error_feedback"]
                base["repair_attempt"] = repair["repair_attempt"]
                if repair.get("needs_human_intervention"):
                    base["needs_human_intervention"] = True
        elif decision == "reject" and pending:
            tool_results = (
                [
                    *tool_results,
                    {
                        "id": pending.get("call_id"),
                        "name": pending.get("name"),
                        "ok": False,
                        "error": "user_rejected",
                    },
                ]
            )[-self._max_results_kept :]
        return {**base, "tool_results": tool_results}

    def _pause(self, pending_call: dict, *, tool_turn_count: int, tool_results: list[dict]) -> dict:
        """写 / 高危调用：暂停循环，交 hitl_gate 发起审批。"""
        return {
            "pending_tool_call": pending_call,
            "awaiting_approval": True,
            "approval_id": None,
            "plan": [pending_call],  # hitl_gate 用 next_step 读 pending call
            "current_step_index": 0,
            "tool_loop_active": True,
            "tool_turn_count": tool_turn_count,
            "tool_results": tool_results,
            "trace": [
                record_trace(
                    "tool_orchestrator",
                    "running",
                    reason="awaiting_hitl",
                    name=pending_call.get("name"),
                )
            ],
        }

    # ---- 状态增量构造 ------------------------------------------------------

    def _continue(
        self,
        *,
        tool_turn_count: int,
        tool_results: list[dict] | None = None,
        load_stage: str | None = None,
        full_toolset_loaded: bool | None = None,
        registered_tools: list[dict] | None = None,
        trace: list[dict] | None = None,
    ) -> dict:
        out: dict[str, Any] = {
            "tool_loop_active": True,
            "tool_turn_count": tool_turn_count,
            "trace": trace or [],
        }
        if tool_results is not None:
            out["tool_results"] = tool_results
        if load_stage is not None:
            out["load_stage"] = load_stage
        if full_toolset_loaded is not None:
            out["full_toolset_loaded"] = full_toolset_loaded
        if registered_tools is not None:
            out["registered_tools"] = registered_tools
        return out

    def _done(
        self,
        *,
        final_answer: str | None,
        tool_turn_count: int,
        tool_results: list[dict],
        trace: list[dict] | None = None,
    ) -> dict:
        out: dict[str, Any] = {
            "tool_loop_active": False,
            "tool_turn_count": tool_turn_count,
            "tool_results": tool_results,
            "trace": trace or [],
        }
        if final_answer is not None:
            out["final_answer"] = final_answer
        return out

    def _format_results(self, results: list[dict]) -> list[dict]:
        """把工具结果压成轻量摘要（截断 + 限量），注入上下文。"""
        out: list[dict] = []
        for r in results[-self._max_results_kept :]:
            summary = r.get("result")
            if isinstance(summary, str):
                summary = summary[: self._max_result_chars]
            out.append(
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "ok": r.get("ok"),
                    "error": r.get("error"),
                    "result": summary,
                }
            )
        return out
