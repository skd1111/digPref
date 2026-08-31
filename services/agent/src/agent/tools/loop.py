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

import asyncio
import json
import logging
from typing import Any

from agent.config import settings
from agent.dual.repair import validate_written_files  # Phase 18 Auto-Repair
from agent.graph.state import record_trace
from agent.llm.json_discipline import strip_think_blocks
from agent.llm.prompts import current_time_text, normalize_message
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


# ---- 轮次预算与停滞熔断（2026-08-25）----------------------------------
# 预算文案诚实化：此前一句「请缩小问题范围」把预算不足甩锅给用户；
# 长链任务（如 PPT 生成 10+ 步）在旧默认 8 轮下必被误杀，默认已提至 24。
# 停滞熔断：连续 _STAGNANT_LIMIT 轮零成功执行才是死循环特征 → 提前终止；
# 每轮都有进展的任务不受影响（预算内不设进度门槛）。
_STAGNANT_LIMIT = 3

# 重复调用熔断（根治 BUGFIX #165）：同一工具 + 同一参数连续调用达此次数即掐断。
# 背景：停滞熔断只看「本轮有没有工具返回 ok」，而 shell 曾把「成功启动一个失败的
# 命令」也算 ok（已在 builtin/shell.py 修正）。实测一次 PPT 任务里模型对着同一个
# python 脚本换了 22 种写法（换解释器 / 写 .bat 包装 / cmd /c / ^ 转义空格），
# 每次 ok=True，熔断计数器一次没涨，24 轮预算烧光，一页 PPT 都没做。
#
# 这一层不依赖 ok —— 只看「是不是在原地打转」。即使将来某个工具的 ok 语义再出问题，
# 重复调用也会被拦住。阈值 3 与 _STAGNANT_LIMIT 对齐（试三次还不换路子就是死循环）。
_REPEAT_CALL_LIMIT = 3


def _call_fingerprint(call: dict) -> str:
    """工具调用指纹：工具名 + 规范化参数。用于识别「原地打转」。

    参数用 sort_keys 的紧凑 JSON，保证 dict 顺序不同但内容相同的调用指纹一致。
    不可序列化的参数退化成 repr（宁可指纹偏保守，也不能抛异常打断工具循环）。
    """
    name = str(call.get("name") or call.get("tool") or "")
    args = call.get("args")
    if args is None:
        args = {k: v for k, v in call.items() if k not in ("name", "tool", "server", "call_id")}
    try:
        args_text = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        args_text = repr(args)
    return f"{name}|{args_text}"


def _count_trailing_repeats(fingerprints: list[str]) -> int:
    """末尾连续相同指纹的长度（空列表返 0）。"""
    if not fingerprints:
        return 0
    last = fingerprints[-1]
    n = 0
    for fp in reversed(fingerprints):
        if fp != last:
            break
        n += 1
    return n


def _repeat_msg(n: int, call: dict) -> str:
    name = str(call.get("name") or call.get("tool") or "该工具")
    return (
        f"检测到同一调用（{name}，参数完全相同）连续重复 {n} 次，已停止以避免空转。\n\n"
        "同一条命令重试不会得到不同结果 —— 请改变方法：\n"
        "- 命令失败时先读 `error` 字段，它给出了真实原因\n"
        "- 列目录 / 查文件 / 读文件优先用 builtin_list_dir / builtin_find / builtin_read_file，"
        "它们不受 shell 引号规则影响\n"
        "- 若确实缺少环境依赖（解释器、命令不存在），直接告知用户而不是继续试"
    )


def _budget_exhausted_msg(turns: int) -> str:
    return (
        f"工具编排预算已用尽（共执行 {turns} 轮）。已完成的步骤均已保留，剩余步骤暂停。"
        "若任务尚未完成，直接再发一句「继续」即可从断点接续，或把任务拆小分步发送。"
    )


def _stagnant_msg(n: int) -> str:
    return (
        f"最近连续 {n} 轮工具执行均无有效结果（重复失败或无可执行动作），"
        "已暂停继续重试以避免空转。请补充关键信息或换一种表述。"
    )


def _pending_model_call_id(pending: dict | None) -> str:
    """HITL 审批后重建调用时用的 tool_call id（BUGFIX #139）。

    优先模型下发的 tool_call id（model_call_id，暂停时暂存）；缺失时回退
    dispatcher 内部 call_id。原因：OpenAI 协议要求 tool 消息的 tool_call_id 必须
    与前面 assistant 消息 tool_calls[].id 配对，assistant 消息里存的是模型 id；
    若用 dispatcher 现生成的 uuid，云端（如 MiniMax）严格校验直接 400：
    "tool result's tool id(...) not found" → 整轮工具循环硬停。
    """
    if not pending:
        return "pending"
    return str(pending.get("model_call_id") or pending.get("call_id") or "pending")


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

# 模型后端瞬时故障重试（2026-08-26，BUGFIX #157）：ReadError/断连等抖动错误
# 重试后再判死，避免几十步工具成果被一次网络抖动直接报废。
_NATIVE_BACKEND_RETRIES = 3
_TRANSIENT_ERROR_HINTS = (
    "readerror",
    "connecterror",
    "remoteprotocolerror",
    "readtimeout",
    "connecttimeout",
    "pooltimeout",
    "timeout",
    "connection",
    "broken pipe",
    "connection reset",
)


def _is_transient_backend_error(exc: BaseException) -> bool:
    """网络抖动/连接断开类瞬时故障可重试；业务错误（参数错/协议错）不重试。"""
    try:
        import httpx

        if isinstance(
            exc,
            (
                httpx.ReadError,
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
                httpx.PoolTimeout,
            ),
        ):
            return True
    except Exception:
        pass
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(h in text for h in _TRANSIENT_ERROR_HINTS)


def _active_skill_addon(state: dict) -> str:
    """活跃 skill 规范注入段（2026-08-26，BUGFIX #155）。

    此前 intent 命中 skill 后只写 state + 发 SSE 徽章，skill.system_prompt
    从未进入工具循环 → 模型裸生成（PPT 无设计感）或脱离规范自由追问（主语错乱）。
    现把 skill 规范拼成执行纪律段，由 native/提示词协议两条循环路径注入。
    未命中 / 加载失败 → 空串（绝不阻断主链路）。
    """
    sid = str(state.get("active_skill_id") or "").strip()
    if not sid:
        return ""
    try:
        from agent.skills import api as skills_api

        skill = skills_api._loader.get(sid) if skills_api._loader else None
        if not skill or not skill.system_prompt:
            return ""
        lines = [f"【已绑定 Skill：{skill.name}（本任务必须严格执行以下规范）】"]
        lines.append(skill.system_prompt.strip())
        examples = list(getattr(skill, "few_shot_examples", None) or [])[:3]
        if examples:
            lines.append("参考示例：")
            for ex in examples:
                role = getattr(ex, "role", "") or (ex.get("role") if isinstance(ex, dict) else "")
                content = getattr(ex, "content", "") or (
                    ex.get("content") if isinstance(ex, dict) else ""
                )
                lines.append(f"[{role}] {str(content)[:600]}")
        lines.append(
            "（执行要求：严格按该 Skill 的工具编排与纪律执行；生成类任务先声明约束再动手，"
            "不要反过来问用户怎么生成；需要追问时，问题主语必须与用户请求的主语一致，"
            "例如用户说「介绍你自己的ppt」，主体就是本助手自身，禁止偷换成用户本人。）"
        )
        return "\n".join(lines)
    except Exception:
        return ""


def _merge_extra_rules(state: dict) -> str:
    """提示词协议循环的 EXTRA_RULES 通道：双模式纪律 + 活跃 skill 规范 + 历史经验（各自非空才拼）。

    Phase 19 V0：历史经验注入（自进化 L1）——按当前意图/技能检索经验库，
    失败静默返空，不影响主链路（见 agent/evolution/memory.py）。
    """
    from agent.evolution.memory import experience_addon

    parts = [
        str(state.get("dual_rules_addon") or "").strip(),
        _active_skill_addon(state).strip(),
        experience_addon(state).strip(),
    ]
    return "\n\n".join(p for p in parts if p)


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
    "8. 回答必须使用与用户输入一致的语言；用户用中文提问时一律中文作答，"
    "禁止整段英文输出（代码块、数字、标识符与专有名词保持原样，不翻译）。\n"
    "9. 你的身份是「EAIDE 企业 AI 助理」智能体（EAIDE 企业 AI IDE 客户端）。"
    "用户要求「介绍你自己」时，介绍的是这个智能体本身（能力/纪律/边界），"
    "严禁把底层模型的名称或厂商当作自我介绍的主体、标题或出品方。\n"
    "10. 多步骤任务（预计 ≥3 个工具调用，如生成文档/批量操作）：开工前先调 "
    "update_todos 拆成 3~8 条简明待办；每完成一项立即再次调用更新状态，"
    "让用户实时看到进度；全部完成后输出最终回答。单步简单任务不必使用。"
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

# 任务进度待办伪工具（2026-08-25）：模型把多步任务拆成待办并实时更新，
# 前端渲染进度卡片；不真执行任何操作，经 trace 通道（todos 字段）下发。
_UPDATE_TODOS_TOOL = {
    "type": "function",
    "function": {
        "name": "update_todos",
        "description": (
            "更新任务进度待办列表（用户会实时看到）。多步任务开工前先拆分，"
            "每完成一项再调一次更新状态；每次传全量列表。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "全量待办列表（3~8 条为宜）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "简明步骤描述"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done"],
                            },
                        },
                        "required": ["content", "status"],
                    },
                }
            },
            "required": ["items"],
        },
    },
}

_TODO_STATUSES = frozenset({"pending", "in_progress", "done"})


def _normalize_todos(raw: Any) -> list[dict]:
    """清洗模型下发的待办列表（条数/长度/状态宽容归一，防脏数据进卡片）。"""
    items: list[dict] = []
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            content = str(it.get("content") or "").strip()[:200]
            if not content:
                continue
            status = it.get("status")
            items.append(
                {"content": content, "status": status if status in _TODO_STATUSES else "pending"}
            )
    return items[:30]


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
        # 协作式取消（执行过程可视化）：每轮工具边界检查 run 取消旗标，
        # 命中即短路出循环（同时覆盖 native / 提示词两条协议路径）。
        from agent.graph.stream import is_run_cancelled

        if is_run_cancelled(str(state.get("run_id") or "")):
            return self._done(
                final_answer="任务已被用户停止。",
                tool_turn_count=int(state.get("tool_turn_count") or 0),
                tool_results=list(state.get("tool_results") or []),
                trace=[record_trace("tool_orchestrator", "fail", reason="cancelled_by_user")],
            )
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
                    final_answer=_budget_exhausted_msg(self._max_turns),
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
                # Phase 18：Code/Work 双模式执行纪律（mode_router 注入）；
                # 2026-08-26：活跃 skill 规范一并拼入（4.13 EXTRA_RULES 通道）
                extra_rules=_merge_extra_rules(merged),
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
            # 重复调用熔断（根治 BUGFIX #165，prompt 模式）：跨图轮累计同一调用指纹。
            # 不看 ok —— 反复调同一条失败命令是最明确的死循环信号。
            last_fp = str(merged.get("tool_last_call_fp") or "")
            repeat_streak = int(merged.get("tool_repeat_streak") or 0)
            for _c, _ in executed_pairs:
                fp = _call_fingerprint(_c)
                repeat_streak = repeat_streak + 1 if fp == last_fp else 1
                last_fp = fp
            if repeat_streak >= _REPEAT_CALL_LIMIT and executed_pairs:
                return {
                    **updates,
                    **self._done(
                        final_answer=_repeat_msg(repeat_streak, executed_pairs[-1][0]),
                        tool_turn_count=turn,
                        tool_results=tool_results,
                        trace=[
                            *op_traces,
                            record_trace(
                                "tool_orchestrator",
                                "fail",
                                reason="repeat_call",
                                repeats=repeat_streak,
                            ),
                        ],
                    ),
                }

            # 停滞熔断（2026-08-25，prompt 模式）：跨图轮累计连续零成功轮，
            # 达阈提前终止；有成功执行即清零（与 native 内层计数同一语义）。
            streak = int(merged.get("tool_stagnant_streak") or 0)
            if any(r.get("ok") for r in executed):
                streak = 0
            else:
                streak += 1
                if streak >= _STAGNANT_LIMIT:
                    return {
                        **updates,
                        **self._done(
                            final_answer=_stagnant_msg(streak),
                            tool_turn_count=turn,
                            tool_results=tool_results,
                            trace=[
                                *op_traces,
                                record_trace(
                                    "tool_orchestrator",
                                    "fail",
                                    reason="stagnant",
                                    streak=streak,
                                ),
                            ],
                        ),
                    }
            return {
                **updates,
                **repair_update,
                **self._continue(
                    tool_results=tool_results,
                    tool_turn_count=turn,
                    tool_stagnant_streak=streak,
                    tool_last_call_fp=last_fp,
                    tool_repeat_streak=repeat_streak,
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
        from agent.graph.stream import is_run_cancelled

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
                    final_answer=_budget_exhausted_msg(self._max_turns),
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
            # 活跃 skill 规范（2026-08-26，BUGFIX #155）：命中的技能必须进循环，
            # 否则模型脱离设计规范裸生成 / 自由追问
            skill_addon = _active_skill_addon(merged)
            if skill_addon:
                system += "\n\n" + skill_addon
            # 当前时间注入（BUGFIX #113）：native 循环的 FINAL_ANSWER 由模型
            # 直接透传给用户不经 summarise，不注入时间基准时会凭记忆编造日期
            # （用户问「今天几号」答「10月10日」）。纪律见 _NATIVE_SYSTEM_PROMPT §7。
            system += f"\n\n【当前时间（系统本地，唯一可信基准）】\n{current_time_text()}"
            messages = [{"role": "system", "content": system}]
            for h in (merged.get("messages") or [])[-4:]:
                parsed = normalize_message(h)
                if parsed is None:
                    continue
                h_role, h_content = parsed
                messages.append({"role": h_role, "content": h_content})
            # 编排决策交接（BUGFIX #108）：确认后新轮次的 user_prompt 只有一句
            # 确认文本，把 decompose 已判定的方案拼进去，避免循环丢失上下文
            hint = _decision_hint(merged.get("decompose_decision"))
            native_user_input = str(merged.get("user_prompt") or "")
            if hint:
                native_user_input = f"[编排决策交接]\n{hint}\n\n[用户当前输入]\n{native_user_input}"
            # 选项回复任务背景加固（BUGFIX #140）：ClarifyCard 选项回复的文本只有
            # 「[回答确认问题] 问题 → 选择」，不含原任务目标 —— 实测模型被选项文案里
            # 的「产品价值/客户案例」带偏，把「介绍你自己的 PPT」漂成「要宣传哪个产品」。
            # 从历史里找回最近一条非选项回复的用户消息（即原任务），确定性拼进输入。
            if native_user_input.lstrip().startswith("[回答确认问题]"):
                origin = ""
                for h in merged.get("messages") or []:
                    parsed = normalize_message(h)
                    if parsed is None:
                        continue
                    role, content = parsed
                    if role != "user":
                        continue
                    text = content
                    if text and not text.startswith("[回答确认问题]"):
                        origin = text
                if origin:
                    native_user_input = (
                        f"[任务背景]\n用户早前发起的任务：{origin[:300]}\n"
                        "当前消息是用户对追问的选项回复，请围绕上述原任务继续，"
                        "不要改变任务主体。\n\n" + native_user_input
                    )
            messages.append({"role": "user", "content": native_user_input})
        elif resuming:
            # HITL 恢复：批准的调用放回待执行队首；拒绝的记入结果。
            # id 必须用模型下发的 tool_call id（暂停时暂存，BUGFIX #139）：
            # assistant 消息 tool_calls 里是模型 id，后续 tool 消息用别的 id 配对失败，
            # 云端严格校验直接 400 "tool result's tool id not found"。
            pending = state.get("pending_tool_call")
            decision = state.get("approval_decision")
            if decision == "approve" and pending:
                pending_calls = [
                    {
                        "id": _pending_model_call_id(pending),
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
                        "tool_call_id": _pending_model_call_id(pending),
                        "content": "用户已拒绝执行该操作。",
                    }
                )

        name_map: dict[str, str] = {}  # 清洗后函数名 → 真实工具名
        # HITL 恢复时先执行审批后剩余的调用，再回到模型轮
        calls_to_execute: list[dict] | None = pending_calls if pending_calls else None
        # 批准重放批次：显式注入 approve，避免 dispatcher 二次拦截
        batch_approved = resuming and state.get("approval_decision") == "approve"
        stagnant = 0  # 停滞熔断：连续零成功轮计数（2026-08-25）
        # 重复调用熔断（BUGFIX #165）：按执行顺序记录调用指纹，末尾连续相同即空转
        call_fingerprints: list[str] = []
        for _ in range(self._max_turns):
            # 协作式取消（执行过程可视化）：native 循环内每轮边界也查旗标，
            # 命中返 None → 调用方 run() 顶部旗标检查已先走短路（此时 state 未变，
            # 下一轮循环入口重新判定）；这里直接返终答更直接。
            if is_run_cancelled(str(state.get("run_id") or "")):
                return self._done(
                    final_answer="任务已被用户停止。",
                    tool_turn_count=int(state.get("tool_turn_count") or 0),
                    tool_results=list(state.get("tool_results") or []),
                    trace=[record_trace("tool_orchestrator", "fail", reason="cancelled_by_user")],
                )
            defs = await self._native_tool_defs(full_loaded, name_map)
            # 本轮允许调用的真实工具名（含 MCP）+ 伪工具
            callable_names = (
                set(name_map.values())
                | {"ask_user", "update_todos"}
                | (set() if full_loaded else {"use_more_tools"})
            )
            if calls_to_execute is None:
                resp = None
                last_exc: Exception | None = None
                for attempt in range(_NATIVE_BACKEND_RETRIES):
                    try:
                        resp = await backend.chat_with_tools(messages, defs)
                        last_exc = None
                        break
                    except Exception as exc:
                        last_exc = exc
                        transient = _is_transient_backend_error(exc)
                        logger.warning(
                            "native tool calling backend error (backend=%s, attempt=%d/%d, transient=%s): %s",
                            backend_name,
                            attempt + 1,
                            _NATIVE_BACKEND_RETRIES,
                            transient,
                            exc,
                        )
                        if not transient or attempt == _NATIVE_BACKEND_RETRIES - 1:
                            break
                        await asyncio.sleep(0.5 * (attempt + 1))
                if last_exc is not None:
                    exc = last_exc  # 后端故障：未执行过工具则回退提示词协议（含重试后仍失败）
                    logger.warning("native tool calling failed (backend=%s): %s", backend_name, exc)
                    if not tool_results and not resuming:
                        return None
                    return _emit(
                        self._done(
                            final_answer=(
                                f"与模型的连接瞬时中断（{type(exc).__name__}），重试 "
                                f"{_NATIVE_BACKEND_RETRIES} 次仍未能继续。已完成的 "
                                f"{len(tool_results)} 步结果已保留，你可以重新发送「继续」接着做。"
                            ),
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
                if real_name == "update_todos":
                    # 任务进度伪工具（2026-08-25）：不执行任何操作，把全量待办
                    # 经 trace 通道（todos 字段）下发 → 前端原地更新进度卡片。
                    items = _normalize_todos((call.get("arguments") or {}).get("items"))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": f"已更新待办列表（{len(items)} 项），继续执行任务。",
                        }
                    )
                    if items:
                        done_n = sum(1 for t in items if t["status"] == "done")
                        op_traces.append(
                            record_trace(
                                "todo",
                                "ok",
                                summary=f"任务进度更新：{len(items)} 项（完成 {done_n}）",
                                todos=items,
                            )
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
                    # HITL 暂停：剩余未执行的调用存入上下文，审批后继续。
                    # 把模型下发的 tool_call id 暂存进 pending（BUGFIX #139）：
                    # 恢复后 tool 消息必须用它才能与 assistant tool_calls 配对。
                    remaining = [
                        {"id": c["id"], "name": c["name"], "arguments": c.get("arguments") or {}}
                        for c in calls[i + 1 :]
                    ]
                    paused_call = dict(result["pending_tool_call"])
                    paused_call.setdefault("model_call_id", call["id"])
                    pause = self._pause(
                        paused_call,
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

            # 重复调用熔断（根治 BUGFIX #165）：同一工具 + 同一参数连续 N 次 = 原地打转。
            # 不看 ok —— 即使某个工具的 ok 语义再出问题，也拦得住。放在停滞熔断之前，
            # 因为「反复调同一条失败命令」比「本轮全失败」是更明确的死循环信号。
            for _c, _ in executed_pairs:
                call_fingerprints.append(_call_fingerprint(_c))
            repeats = _count_trailing_repeats(call_fingerprints)
            if repeats >= _REPEAT_CALL_LIMIT and executed_pairs:
                return _emit(
                    self._done(
                        final_answer=_repeat_msg(repeats, executed_pairs[-1][0]),
                        tool_turn_count=turn,
                        tool_results=tool_results,
                        trace=[
                            record_trace(
                                "tool_orchestrator",
                                "fail",
                                reason="repeat_call",
                                mode="native",
                                repeats=repeats,
                            )
                        ],
                    )
                )

            # 停滞熔断（2026-08-25）：连续 _STAGNANT_LIMIT 轮零成功执行 = 空转，
            # 提前终止；任一轮有成功执行即清零（有进展的任务不受影响）。
            if any(r.get("ok") for r in executed):
                stagnant = 0
            else:
                stagnant += 1
                if stagnant >= _STAGNANT_LIMIT:
                    return _emit(
                        self._done(
                            final_answer=_stagnant_msg(stagnant),
                            tool_turn_count=turn,
                            tool_results=tool_results,
                            trace=[
                                record_trace(
                                    "tool_orchestrator",
                                    "fail",
                                    reason="stagnant",
                                    mode="native",
                                    streak=stagnant,
                                )
                            ],
                        )
                    )

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
                final_answer=_budget_exhausted_msg(self._max_turns),
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
        tools: list[dict] = [_ASK_USER_TOOL, _UPDATE_TODOS_TOOL]
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
        tool_stagnant_streak: int | None = None,
        tool_last_call_fp: str | None = None,
        tool_repeat_streak: int | None = None,
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
        if tool_stagnant_streak is not None:
            out["tool_stagnant_streak"] = tool_stagnant_streak
        # 重复调用熔断跨图轮状态（BUGFIX #165）：prompt 模式每轮是独立节点执行，
        # 指纹与计数必须存进 state 才能跨轮累计。
        if tool_last_call_fp is not None:
            out["tool_last_call_fp"] = tool_last_call_fp
        if tool_repeat_streak is not None:
            out["tool_repeat_streak"] = tool_repeat_streak
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
