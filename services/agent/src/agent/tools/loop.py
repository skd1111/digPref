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

from typing import Any

from agent.config import settings
from agent.dual.repair import validate_written_files  # Phase 18 Auto-Repair
from agent.graph.state import record_trace
from agent.llm.router import LMRouter


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
        """运行一轮循环（可能包含 HITL 暂停/恢复），返回 AgentState 增量。"""
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
                # Phase 18：Code/Work 双模式执行纪律（mode_router 注入）
                extra_rules=str(merged.get("dual_rules_addon") or ""),
                # 运行时上下文：工作模式 / 自主级别 / 任务路由（提示词 4.10–4.12）
                work_mode=str(merged.get("work_mode") or ""),
                autonomy=str(merged.get("autonomy") or ""),
                routing=str(merged.get("routing") or ""),
            )
        except Exception as exc:  # noqa: BLE001 —— 编排器异常不阻塞主流程
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
                    trace=[record_trace(
                        "tool_orchestrator", "ok",
                        action="SELECT_TOOLS", selected=len(defs),
                    )],
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
                    trace=[record_trace(
                        "tool_orchestrator", "ok",
                        action="REQUEST_FULL_TOOLS", registered=len(defs),
                    )],
                ),
            }

        if kind == "TOOL_CALLS":
            registered_names = {str(t.get("name")) for t in registered}
            executed: list[dict] = []
            executed_pairs: list[tuple[dict, dict]] = []  # Phase 18：供 Auto-Repair 钩子
            for call in action.get("tool_calls") or []:
                name = str(call.get("name") or "")
                if name not in registered_names:
                    executed.append({
                        "id": call.get("id"),
                        "name": name,
                        "ok": False,
                        "error": "unregistered_tool",
                    })
                    continue
                result = await self._catalog.execute(
                    name,
                    dict(call.get("arguments") or {}),
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
                        "trace": repair["trace"] + [record_trace(
                            "tool_orchestrator", "fail", reason="repair_exhausted",
                        )],
                    }
            return {
                **updates,
                **repair_update,
                **self._continue(
                    tool_results=tool_results,
                    tool_turn_count=turn,
                    trace=[record_trace(
                        "tool_orchestrator", "ok",
                        action="TOOL_CALLS", calls=len(executed),
                    )] + (repair["trace"] if repair else []),
                ),
            }

        if kind == "ASK_USER":
            return {
                **updates,
                **self._done(
                    final_answer=action.get("ask_user_message") or "需要补充信息后才能继续。",
                    tool_turn_count=turn,
                    tool_results=tool_results,
                    trace=[record_trace("tool_orchestrator", "ok", action="ASK_USER")],
                ),
            }

        # FINAL_ANSWER
        answer = str(action.get("final_answer") or "")
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
            return {**base, "awaiting_approval": True, "pending_tool_call": pending, "plan": [pending] if pending else []}
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
            tool_results = (tool_results + [result])[-self._max_results_kept :]
            # Phase 18 Auto-Repair：审批后执行的写操作同样要验证
            repair = validate_written_files(state, [(pending, result)])
            if repair:
                tool_results = (tool_results + repair["extra_results"])[-self._max_results_kept :]
                base["error_feedback"] = repair["error_feedback"]
                base["repair_attempt"] = repair["repair_attempt"]
                if repair.get("needs_human_intervention"):
                    base["needs_human_intervention"] = True
        elif decision == "reject" and pending:
            tool_results = (tool_results + [{
                "id": pending.get("call_id"),
                "name": pending.get("name"),
                "ok": False,
                "error": "user_rejected",
            }])[-self._max_results_kept :]
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
            "trace": [record_trace(
                "tool_orchestrator", "running",
                reason="awaiting_hitl",
                name=pending_call.get("name"),
            )],
        }

    # ---- 状态增量构造 ------------------------------------------------------

    def _continue(self, *, tool_turn_count: int, tool_results: list[dict] | None = None,
                  load_stage: str | None = None, full_toolset_loaded: bool | None = None,
                  registered_tools: list[dict] | None = None,
                  trace: list[dict] | None = None) -> dict:
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

    def _done(self, *, final_answer: str | None, tool_turn_count: int,
              tool_results: list[dict], trace: list[dict] | None = None) -> dict:
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
            out.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "ok": r.get("ok"),
                "error": r.get("error"),
                "result": summary,
            })
        return out
