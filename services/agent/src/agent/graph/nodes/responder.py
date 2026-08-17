"""responder node — synthesise the final natural-language answer.

Two paths:
    - Intent = chitchat OR empty plan  →  respond directly from history
    - Otherwise                        →  call LLM.summarise(plan, results)
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent.graph.nodes.repair import MAX_RETRIES
from agent.graph.state import AgentState, record_trace
from agent.llm.router import LMRouter


async def responder_node(state: AgentState, llm: LMRouter) -> dict:
    intent = state.get("intent")
    plan = state.get("plan") or []
    sub_agent_reports = state.get("sub_agent_reports") or []
    tool_results = _collect_results(state)
    user_prompt = state.get("user_prompt", "")

    # Phase 12 V2：编排决策终态优先（追问 / 拒绝 / 确认门槛 / 主智能体直接回答）
    decision = state.get("decompose_decision") or {}
    inner = decision.get("decision") if isinstance(decision, dict) else {}
    if isinstance(inner, dict):
        mode = inner.get("mode")
        if mode == "REFUSE":
            return _terminal_answer(
                inner.get("refusal_message") or "该请求无法执行。",
                "refuse",
                state,
            )
        if mode == "ASK_USER":
            questions = inner.get("clarifying_questions") or []
            return _terminal_answer(_ask_user_body(questions), "ask_user", state)
        if inner.get("user_confirmation_required") is True:
            message = inner.get("confirmation_message") or "该任务需要您确认后才能执行。"
            return _terminal_answer(_confirmation_body(message), "confirmation", state)
        # 语义路由命中的闲聊 → 模板直回，零 LLM（semantic_route canned_response）
        if mode == "MAIN_AGENT" and isinstance(state.get("intent_analysis"), dict):
            canned = str((state["intent_analysis"] or {}).get("canned_response") or "")
            if canned.strip():
                return _terminal_answer(canned, "semantic_route", state)
        if mode == "MAIN_AGENT":
            return await _answer_directly(state, llm)

    # 动态工具循环：FINAL_ANSWER / ASK_USER 已由编排器产出 → 直接透传
    if state.get("final_answer"):
        draft = str(state["final_answer"])
        # 语言硬兜底（BUGFIX #114，2026-08-17）：prompt 语言纪律对小模型是软
        # 约束，中文提问仍可能拿到整段英文终答 → 透传前检测，命中改走
        # summarise 重写中文（summarise.md 带 MANDATORY 中文约束）
        if _needs_chinese_rewrite(user_prompt, draft):
            return await _rewrite_to_chinese(state, llm, draft)
        return _terminal_answer(draft, "tool_loop", state)

    # 动态工具循环：有工具结果 → 汇总成最终答案
    if state.get("tool_results"):
        return await _synthesise_tool_results(state, llm, state["tool_results"])

    # Special-case: chitchat with no tool work
    if intent == "chitchat" or not plan:
        if intent == "chitchat":
            return {
                "final_answer": "你好，我是 EAIDE 企业 AI 助理。告诉我你想查询或操作哪个系统吧。",
                "sources": [],
                "trace": [record_trace("responder", "ok", mode="chitchat")],
            }
        # Phase 12 V2：多智能体模式 —— 汇总各子 Agent 回报
        if sub_agent_reports:
            return await _respond_from_sub_agents(
                state,
                llm,
                sub_agent_reports,
                user_prompt,
            )
        return {
            "final_answer": "抱歉，没有可执行的工具调用来回答这个问题。",
            "sources": [],
            "trace": [record_trace("responder", "ok", mode="empty_plan")],
        }

    # Hard-fail cases (HITL reject or retry exhausted)
    hard_fail = _check_hard_failures(state)
    if hard_fail:
        return {
            "final_answer": hard_fail,
            "sources": [step.get("server") for step in plan if step.get("server")],
            "trace": [record_trace("responder", "ok", mode="hard_fail")],
        }

    try:
        answer, sources = await llm.summarise(
            intent=intent,
            user_prompt=user_prompt,
            plan=plan,
            results=tool_results,
        )
    except Exception as exc:
        if tool_results:
            body = (
                f"我无法综合最终答案（{type(exc).__name__}）。"
                f"下面是工具返回的原始结果：\n\n{tool_results!r}"
            )
        else:
            body = _no_model_answer(exc)
        return {
            "final_answer": body,
            "sources": [],
            "trace": [record_trace("responder", "fail", error=str(exc))],
        }

    return {
        "final_answer": answer,
        "sources": sources,
        "truncated_any": state.get("truncated_any", False),
        "trace": [record_trace("responder", "ok", sources=len(sources))],
    }


# ---- Helpers ---------------------------------------------------------------


def _no_model_answer(exc: Exception) -> str:
    """LLM 全链不可用时给用户的终答 —— 必须可操作，不能静默。

    router.summarise 全链失败时抛的错误文本已带「无可用模型」前缀，
    直接透传；其他异常包一层通用排查指引。
    """
    detail = str(exc)
    if "无可用模型" in detail:
        return detail
    return (
        "当前无可用模型：所有 LLM 后端均不可用。\n\n"
        "请检查：\n"
        "1. 本地 Ollama 是否已安装模型；\n"
        "2. 内网 / 云端模型网关是否可达，「设置 → 模型管理」中是否已配置可用后端。\n\n"
        f"技术详情：{type(exc).__name__}: {detail}"
    )


def _terminal_answer(body: str, mode: str, state: AgentState) -> dict:
    return {
        "final_answer": body,
        "sources": [],
        "trace": [record_trace("responder", "ok", mode=mode)],
    }


# ---- 语言硬兜底（BUGFIX #114，2026-08-17）------------------------------------
#
# 内网推理模型默认英文作答，工具循环 FINAL_ANSWER 透传不经 summarise，prompt
# 语言纪律（_NATIVE_SYSTEM_PROMPT §8 / tool_orchestrate §6.8）对小模型只是软
# 约束。这里在透传前做确定性检测：中文提问 + 终答几乎纯英文 → 改走 summarise
# 重写中文；重写失败/无效一律回退原文，绝不阻断终答。

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _needs_chinese_rewrite(user_prompt: str, answer: str) -> bool:
    """中文提问 + 终答以英文为主 → True。

    判定保守（宁可漏改不可误伤）：
    - 提问不含中文 → 不改（英文提问英文答是合法的）；
    - 终答含 ```clarify 选项卡 → 不改（重写会破坏前端卡片 JSON 结构）；
    - 终答无拉丁字母（纯数字/符号/中文）→ 不改；
    - 拉丁字母数 ≥ 20 且超过中文字符数 3 倍 → 判为英文作答。
    """
    if not _CJK_RE.search(user_prompt):
        return False
    if "```clarify" in answer:
        return False
    cjk = len(_CJK_RE.findall(answer))
    latin = len(_LATIN_RE.findall(answer))
    if latin == 0:
        return False
    return latin >= 20 and latin > cjk * 3


async def _rewrite_to_chinese(state: AgentState, llm: LMRouter, draft: str) -> dict:
    """把英文终答草稿交 summarise 重写为中文（其提示词带 MANDATORY 中文约束）。

    重写异常 / 重写结果仍不含中文（模型不听话）→ 回退原草稿，不劣化终答。
    """
    try:
        answer, sources = await llm.summarise(
            intent=state.get("intent") or "query",
            user_prompt=state.get("user_prompt", ""),
            plan=[],
            results=[{"tool": "language_rewrite", "ok": True, "result": draft}],
        )
    except Exception:
        return _terminal_answer(draft, "tool_loop", state)
    if not str(answer).strip() or not _CJK_RE.search(str(answer)):
        return _terminal_answer(draft, "tool_loop", state)
    return {
        "final_answer": str(answer),
        "sources": sources,
        "trace": [record_trace("responder", "ok", mode="tool_loop", lang_rewrite=True)],
    }


def _confirmation_body(message: str) -> str:
    """确认门槛终答：参数摘要正文 + 确认卡（确认 / 修改）。

    复用前端 ClarifyCard（```clarify 围栏）：点「确认执行」→ 选项文本回发
    继续任务；点「修改参数」→ 卡片自带自定义输入框直接说要改什么。
    真正的写操作仍会再过 HITL 审批闸（红线：写操作绝不绕过 HITL）。
    """
    items = [
        {
            "question": "确认按上述参数执行？",
            "options": [
                {
                    "text": "确认执行",
                    "reason": "参数摘要核对无误，继续执行",
                    "recommended": True,
                },
                {
                    "text": "修改参数",
                    "reason": "选这项并在下方直接告诉我要改什么",
                    "recommended": False,
                },
            ],
        }
    ]
    block = json.dumps(items, ensure_ascii=False, indent=2)
    return f"{message}\n\n（未确认前不会执行任何操作。）\n\n```clarify\n{block}\n```"


# ---- ASK_USER 选项卡化（2026-08-14）--------------------------------------
#
# 编排决策器的 clarifying_questions 是自由文本，常自带 a/b/c 选项枚举（如
# 「环境缺工具；a．您先手动 ping…；b．改用…；c．跳过…」）。此前只拼正文
# bullet list，不走 FINAL_ANSWER_STYLE 的 clarify 约定 → 前端无卡片可点。
# 这里做确定性解析（不调 LLM、不改 prompt，LLM 输出不稳定只在解析层兼容）：
# 从问题文本拆出题干 + 选项，拼出前端 ClarifyCard 认识的 ```clarify 块；
# 解析不出选项时用问题原文做单选项兜底（卡片另有自定义输入框）。

# 字母选项标记：a/b/c…（大小写）后接 ．.、)）：: 或空格，且前面是
# 行首/空白/分隔标点（防误伤普通单词里的单字母）
_OPT_MARK_RE = re.compile(r"(?:^|[\s；;，,。:：])(([a-iA-I])[．.、)）:：\s])")


def _split_lettered_options(question: str) -> tuple[str, list[str]]:
    """把「题干…；a．…；b．…」拆成 (题干, 选项列表)；无选项返 (问题, [])。"""
    marks = list(_OPT_MARK_RE.finditer(question))
    if len(marks) < 2:
        return question, []
    # 题干 = 首个标记前的正文（匹配含前导分隔符，切片天然不含它）
    stem = question[: marks[0].start()].strip(" 　\t；;。，,、:：") or "请选择如何继续"
    # 选项 = 相邻标记之间的文本段（m.end() 已含字母后的分隔符）
    options: list[str] = []
    for i, m in enumerate(marks):
        seg_end = marks[i + 1].start() if i + 1 < len(marks) else len(question)
        text = question[m.end() : seg_end].strip(" 　\t；;。，,、:：")
        if text:
            options.append(text)
    if len(options) < 2:
        return question, []
    return (stem, options[:5])


def _ask_user_body(questions: list) -> str:
    """ASK_USER 终答：可读 bullet 列表 + clarify 选项块（前端渲染可点选卡片）。"""
    qs = [str(q).strip() for q in questions if str(q).strip()]
    if not qs:
        return "需要补充更多信息后才能继续，请补充说明您的需求。"
    bullets = "\n".join(f"- {q}" for q in qs)
    body = f"需要补充以下信息后再继续：\n\n{bullets}"

    items: list[dict] = []
    for q in qs:
        stem, options = _split_lettered_options(q)
        if not options:
            options = [q]
        items.append(
            {
                "question": stem,
                "options": [
                    {"text": o, "reason": "", "recommended": i == 0} for i, o in enumerate(options)
                ],
            }
        )
    block = json.dumps(items, ensure_ascii=False, indent=2)
    return f"{body}\n\n```clarify\n{block}\n```"


async def _synthesise_tool_results(
    state: AgentState,
    llm: LMRouter,
    results: list[dict],
) -> dict:
    """把动态工具循环的执行结果交给 summarise 综合成最终答案。"""
    rows: list[dict] = []
    for r in results:
        rows.append(
            {
                "tool": r.get("name"),
                "ok": r.get("ok"),
                "error": r.get("error"),
                "result": _truncate_result(r.get("result")),
            }
        )
    try:
        answer, sources = await llm.summarise(
            intent=state.get("intent") or "query",
            user_prompt=state.get("user_prompt", ""),
            plan=[],
            results=rows,
        )
    except Exception as exc:
        return {
            "final_answer": _no_model_answer(exc)
            if not rows
            else (f"我无法综合工具结果（{type(exc).__name__}）。以下是工具原始返回：\n\n{rows!r}"),
            "sources": [],
            "trace": [record_trace("responder", "fail", mode="tool_loop", error=str(exc))],
        }
    return {
        "final_answer": answer,
        "sources": sources,
        "trace": [record_trace("responder", "ok", mode="tool_loop", tools=len(rows))],
    }


def _truncate_result(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + " …（已截断）"
    return value


async def _answer_directly(state: AgentState, llm: LMRouter) -> dict:
    """MAIN_AGENT：由主智能体直接回答（不调用工具、不派生子智能体）。"""
    try:
        answer, sources = await llm.summarise(
            intent=state.get("intent") or "query",
            user_prompt=state.get("user_prompt", ""),
            plan=[],
            results=[],
        )
    except Exception as exc:
        return {
            "final_answer": _no_model_answer(exc),
            "sources": [],
            "trace": [record_trace("responder", "fail", mode="main_agent", error=str(exc))],
        }
    return {
        "final_answer": answer,
        "sources": sources,
        "trace": [record_trace("responder", "ok", mode="main_agent")],
    }


async def _respond_from_sub_agents(
    state: AgentState,
    llm: LMRouter,
    reports: list[dict],
    user_prompt: str,
) -> dict:
    """把自动派生的子 Agent 回报整理成结果集，交给 summarise 综合成最终答案。"""
    rows: list[dict] = []
    for r in reports:
        rows.append(
            {
                "sub_agent_id": r.get("sub_agent_id", ""),
                "task_type": _report_task_type(r),
                "status": r.get("status", ""),
                "summary": r.get("summary", ""),
                "error_message": r.get("error_message", ""),
                "confidence": r.get("confidence", 0.0),
                "latency_ms": r.get("latency_ms", 0),
            }
        )

    try:
        answer, sources = await llm.summarise(
            intent=state.get("intent") or "query",
            user_prompt=user_prompt,
            plan=[],
            results=rows,
        )
    except Exception as exc:
        fallback = "\n\n".join(
            f"**{r.get('sub_agent_id', '?')}**（{r.get('task_type', '?')}）:\n"
            f"{r.get('summary') or r.get('error_message') or '(空回报)'}"
            for r in rows
        )
        return {
            "final_answer": (
                f"我无法综合子智能体的结果（{type(exc).__name__}）。"
                f"以下是各子智能体的原始回报：\n\n{fallback}"
            ),
            "sources": [],
            "trace": [record_trace("responder", "fail", mode="multi_agent", error=str(exc))],
        }

    return {
        "final_answer": answer,
        "sources": sources,
        "multi_agent": True,
        "trace": [
            record_trace(
                "responder",
                "ok",
                mode="multi_agent",
                sub_agents=len(rows),
            )
        ],
    }


def _report_task_type(report: dict) -> str:
    state_delta = report.get("state_delta") or {}
    fields = state_delta.get("fields_added") or {}
    return str(fields.get("task_type") or report.get("task_type") or "?")


def _collect_results(state: AgentState) -> list[dict]:
    """Collect tool results from all executed steps.

    Uses trace entries as the primary source (each tool_runner 'ok' trace
    records the result), then falls back to the current tool_result for
    backward compatibility.
    """
    results: list[dict] = []
    seen = set()

    # 1. Collect from trace entries (multi-step support)
    for entry in state.get("trace", []):
        if entry.get("node") == "tool_runner" and entry.get("status") == "ok":
            result = entry.get("result")
            if result and isinstance(result, dict):
                call_id = entry.get("call_id", "")
                if call_id and call_id not in seen:
                    seen.add(call_id)
                    results.append(result)

    # 2. Fallback: include current tool_result if not already captured
    current = state.get("tool_result")
    if current is not None and id(current) not in seen:
        results.append(current)

    return results


def _check_hard_failures(state: AgentState) -> str | None:
    if state.get("approval_decision") == "reject":
        call = state.get("pending_tool_call") or {}
        return (
            f"用户已**拒绝**执行 `{call.get('server')}.{call.get('name')}` 操作，本次任务已取消。"
        )
    if state.get("tool_error") and state.get("retry_count", 0) >= MAX_RETRIES:
        call = state.get("pending_tool_call") or {}
        return (
            f"工具 `{call.get('server')}.{call.get('name')}` 在自动重试 "
            f"{state.get('retry_count', 0)} 次后仍然失败：\n\n"
            f"```\n{state.get('tool_error')}\n```\n\n"
            "请换个问法或联系运维。"
        )
    return None
