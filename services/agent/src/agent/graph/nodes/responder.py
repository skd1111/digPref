"""responder node — synthesise the final natural-language answer.

Two paths:
    - Intent = chitchat OR empty plan  →  respond directly from history
    - Otherwise                        →  call LLM.summarise(plan, results)
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from agent.config import settings
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
        # 选项卡硬兜底（BUGFIX #136）：模型自由文本里的选项枚举（o1/o2/o3、
        # 选项A/B/C）不走 ASK_USER 结构路径也不输出 clarify 围栏 → 前端无卡可点，
        # 透传前确定性补一块 ```clarify（解析不出不动原文）
        draft = _attach_clarify_from_text(draft)
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
            # 会话中段的「闲聊」多半是纠偏/确认类短句被关键词启发式误判（本地意图模型
            # 缺席时 analyze_intent 退化到 mock/plain，见 BUGFIX #140）——模板直回会吞掉
            # 用户纠正；仅首轮问候保留零 LLM 直回，中段改走带历史的正常回答（#135）。
            if len(state.get("messages") or []) <= 1:
                return {
                    "final_answer": "你好，我是 EAIDE 企业 AI 助理。告诉我你想查询或操作哪个系统吧。",
                    "sources": [],
                    "trace": [record_trace("responder", "ok", mode="chitchat")],
                }
            try:
                answer, sources = await _summarise_maybe_stream(
                    state,
                    llm,
                    intent="chitchat",
                    user_prompt=user_prompt,
                    plan=[],
                    results=[],
                    history=_history_for_answer(state),
                )
            except Exception:
                # 全链不可用时回退模板，不阻断（宁模板不可空白）
                return {
                    "final_answer": "你好，我是 EAIDE 企业 AI 助理。告诉我你想查询或操作哪个系统吧。",
                    "sources": [],
                    "trace": [record_trace("responder", "fail", mode="chitchat_fallback")],
                }
            return {
                "final_answer": answer,
                "sources": sources,
                "trace": [record_trace("responder", "ok", mode="chitchat_mid_session")],
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
        answer, sources = await _summarise_maybe_stream(
            state,
            llm,
            intent=intent,
            user_prompt=user_prompt,
            plan=plan,
            results=tool_results,
            history=_history_for_answer(state),
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


async def _summarise_maybe_stream(
    state: AgentState, llm: LMRouter, **kwargs: Any
) -> tuple[str, list[str]]:
    """终答汇总（2026-09-03 流式优先）：answer_stream 开启且 llm 支持
    summarise_stream 时逐 token 发 answer_delta 事件；否则原样走 llm.summarise
    （测试替身 / 旧后端零影响）。

    delta 走 builtin.events 进程内队列（stream.py 0.4s 轮询 drain 进 SSE）；
    msg_id 由 stream.py 从首条 delta 种子化到终答 message 事件（#142 同 id
    原地覆盖），流式草稿与终稿收敛为同一条气泡。
    """
    # 知识库召回注入（根因修复 2026-09-04）：rag_retrieve 检索到的召回写入
    # state.system_prompt_addon 后，此前从未进入终答 prompt（summarise 无该参数），
    # 导致「BM25 命中却弹澄清 / 跑去 shell 翻全盘」。在这里单点补上，覆盖
    # MAIN_AGENT / 工具结果汇总 / 子智能体 / 闲聊 全部作答路径；调用方已显式
    # 传入 rag_context 时不覆盖（预留定制）。
    if "rag_context" not in kwargs:
        addon = str(state.get("system_prompt_addon") or "").strip()
        if addon:
            kwargs["rag_context"] = addon
    run_id = str(state.get("run_id") or "")
    if (
        not getattr(settings, "answer_stream_enabled", True)
        or not run_id
        or not hasattr(llm, "summarise_stream")
    ):
        return await llm.summarise(**kwargs)

    from agent.builtin.events import emit_answer_delta

    msg_id = str(uuid.uuid4())

    async def _on_delta(delta: str) -> None:
        try:
            await emit_answer_delta(run_id=run_id, msg_id=msg_id, delta=delta)
        except Exception:  # 推送故障不阻断终答生成（终稿 message 事件兜底）
            pass

    return await llm.summarise_stream(on_delta=_on_delta, **kwargs)


def _history_for_answer(state: AgentState) -> list:
    """终答用会话历史（BUGFIX #135）：取 state["messages"]，去掉与当轮
    user_prompt 相同的末条（避免与 User question 重复）。此前终答链路只看见
    当轮 prompt，跨轮追问（上轮「做个介绍你自己的 PPT」+ 本轮「是你自己这个智能体客户端」）
    模型看不见前文 → 反问「没有明确任务指令」。"""
    msgs = list(state.get("messages") or [])
    user_prompt = str(state.get("user_prompt") or "").strip()
    if msgs and user_prompt:
        last = msgs[-1]
        content = getattr(last, "content", None) or (
            last.get("content") if isinstance(last, dict) else None
        )
        if str(content or "").strip() == user_prompt:
            msgs = msgs[:-1]
    return msgs


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
        answer, sources = await _summarise_maybe_stream(
            state,
            llm,
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


# ---- 自由文本选项枚举 → clarify 选项卡（BUGFIX #136，2026-08-25）----------
#
# 模型自由形式的追问/候选方案常自带选项枚举（如「- o1. 创建空白演示文稿…
# 回复编号即可继续」或「选项A：精简版 / 选项B：标准版」），但既不走 ASK_USER
# 结构路径、也不按 FINAL_ANSWER_STYLE 输出 ```clarify 围栏 → 前端 ClarifyCard
# 无卡可渲染，用户只能手打编号（用户反馈：想要 codex 那样的可点选项卡）。
# 与 _split_lettered_options 同源思路：不调 LLM，只在出口做确定性解析，
# 解析不出选项一律不动原文（宁可漏加不可误伤）。
#
# 支持三种行首标记（各需命中 ≥2 行才采信，防普通正文误伤）：
#   ① 数字编号：1. / 2、/ o1. / O2)（o/O 前缀可选，兼容「回复编号」式枚举）
#   ② 选项字母：选项A：/ 选项 b.（中文场景高频，优先级最高 —— 与数字编号混排时
#      数字行往往是题干分点，选项才是可点项，如真实会话 id=57）
#   ③ 字母编号：A. / b)（行首 + 标记后必须跟分隔符与正文）
# 标记前允许 markdown 加粗包裹（**A. xxx**，BUGFIX #150：云端模型高频输出
# 粗体选项，此前不识别 → 无卡可点，用户手打「A」回复又丢上下文）。
_ENUM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:[-*•]\s*)?(?:\*{1,2}\s*)?选项\s*([A-Ia-i])\s*[：:．.、)）]?\s*(.+)$"),
    re.compile(r"^\s*(?:[-*•]\s*)?(?:\*{1,2}\s*)?[oO]?(\d{1,2})\s*[．.、)）:：]\s*(.+)$"),
    re.compile(r"^\s*(?:[-*•]\s*)?(?:\*{1,2}\s*)?([A-Ia-i])\s*[．.、)）:：]\s+(.+)$"),
)
_ENUM_OPT_MAX = 5
_ENUM_OPT_CHARS = 200
# 选择引导语：数字/字母编号命中时必须同现（防把普通步骤列表误加卡片）；
# 「选项X」格式自带选择语义，不要求引导语。
# BUGFIX #149（2026-08-25）：真实会话扫描发现「请提供以下任一信息即可推进：
# 1. … 2. …」「请您补充以下任一信息即可继续：1. … 2. …」这类高频追问因缺
# 引导语漏卡；「可多选」类多选题同样漏卡 → 补 任一/多选 两类引导语。
# BUGFIX #150（2026-08-26）：补「从下面挑一个」类口语化引导语。
_CHOICE_CUE_RE = re.compile(
    r"回复\s*(编号|数字|字母)|请选择|候选方案|任选其一|任一|选\s*(一|哪)|挑\s*一|选项\s*[（(]|多选"
    # 2026-08-27（用户反馈漏卡）：「请直接回复例如：'10 页 / …'」类参数确认引导语；
    # 仍受 ≥2 行选项样式双门槛保护，普通编号列表不会误加卡片。
    r"|直接回复|回复例如|回复即"
)
# 多选题判定（题干含「多选」类字样 → clarify 项带 multi 标记，前端渲染复选框）
_MULTI_CUE_RE = re.compile(r"多选|多项选择|选择多个|可选多个")

# ---- 多维参数确认式追问（2026-08-27 用户反馈漏卡）----------------------
#
# 「请直接回复例如：'10 页 / 客户介绍 / A / A / 不要'，缺省我会按 '10 页 / 内部分享 / …'
# 执行」—— 编号行是多维参数而非互斥选项，枚举路径即使命中也不宜当选项卡；
# 确定性抽取「缺省默认组合」，生成「按默认执行（推荐）/ 自定义」二元确认卡，
# 点默认项即可继续，不点可手打。抽取不到（无缺省组合或无回复引导语）不动原文。
_CONFIRM_COMBO_RE = re.compile(
    r"(?:缺省|默认|未说明|未指定|不回复|否则)[^。\n]{0,24}?按\s*[「『\"'‘’]?"
    r"([^」』\"'‘’。\n]{2,80}?)[」』\"'‘’]?\s*执行"
)
_CONFIRM_REPLY_CUE_RE = re.compile(r"请直接回复|直接回复|回复.{0,12}即可")


def _confirm_combo_clarify(answer: str) -> str | None:
    """参数确认式终答 → 「按默认配置执行 / 自定义」确认卡 JSON；不构成返 None。"""
    m = _CONFIRM_COMBO_RE.search(answer)
    if not m or not _CONFIRM_REPLY_CUE_RE.search(answer):
        return None
    combo = m.group(1).strip(" \t：:，,、")
    if len(combo) < 2:
        return None
    items = [
        {
            "question": "是否按以下参数配置继续？",
            "options": [
                {"text": f"按默认配置执行：{combo}", "reason": "", "recommended": True},
                {"text": "自定义配置（直接回复各项选择即可）", "reason": "", "recommended": False},
            ],
        }
    ]
    return json.dumps(items, ensure_ascii=False, indent=2)


def _clean_option_text(text: str) -> str:
    """选项正文去 markdown 加粗/反引号 + 截断，保持卡片可读。"""
    cleaned = re.sub(r"[*`]", "", text).strip(" \t；;。，,、:：")
    return cleaned[:_ENUM_OPT_CHARS]


def _extract_enum_options(answer: str) -> tuple[str, list[str], int]:
    """从终答文本提取（题干, 选项列表, 命中的标记格式下标）；不构成枚举返 ("", [], -1)。

    题干 = 首个选项行之前最后一段非空正文（截尾 120 字），缺失时用通用文案；
    优先采信命中行数最多的标记格式，同数时按 _ENUM_PATTERNS 顺序（选项X 优先）。
    """
    lines = answer.split("\n")
    best: tuple[int, list[tuple[int, str]], int] = (0, [], -1)  # (命中数, [(行号, 正文)], 格式)
    for pi, pattern in enumerate(_ENUM_PATTERNS):
        hits = []
        for idx, line in enumerate(lines):
            m = pattern.match(line)
            if m:
                text = _clean_option_text(str(m.group(2)))
                if text:
                    hits.append((idx, text))
        if len(hits) >= 2 and len(hits) > len(best[1]):
            best = (len(hits), hits, pi)
    if len(best[1]) < 2:
        return "", [], -1
    hits = best[1][:_ENUM_OPT_MAX]
    first_line = hits[0][0]
    # 题干：选项行之前最后一段非空正文（跳过紧邻的行内引导语）
    stem = ""
    for line in lines[:first_line]:
        text = line.strip()
        if text:
            stem = re.sub(r"[*`]", "", text)
    stem = stem[-120:].strip(" \t：:—-·") or "请选择如何继续"
    return stem, [text for _, text in hits], best[2]


def _attach_clarify_from_text(answer: str) -> str:
    """终答含自由文本选项枚举 → 末尾追加 ```clarify 块（前端渲染可点选卡片）。

    已有 clarify 围栏 / 解析不出 ≥2 选项 → 原文不动。recommended 标记：
    选项文本含「推荐」字样则标推荐项，否则默认第一项（与 _ask_user_body 对齐）。
    题干含「多选」类字样 → 卡片项带 multi=true，前端按复选框交互（#149）。
    """
    if not answer or "```clarify" in answer:
        return answer
    # 多维参数确认式（优先级最高）：编号行非互斥选项，不走枚举路径，
    # 直接抽缺省默认组合出「按默认执行 / 自定义」确认卡。
    combo_block = _confirm_combo_clarify(answer)
    if combo_block:
        return f"{answer}\n\n```clarify\n{combo_block}\n```"
    stem, options, pattern_idx = _extract_enum_options(answer)
    if len(options) < 2:
        return answer
    # 数字/字母编号格式需同现选择引导语，避免普通步骤列表被误加卡片（宁可漏加）
    if pattern_idx > 0 and not _CHOICE_CUE_RE.search(answer):
        return answer
    recommended_idx = next((i for i, o in enumerate(options) if "推荐" in o), 0)
    items = [
        {
            "question": stem,
            "options": [
                {
                    "text": o,
                    "reason": "",
                    "recommended": i == recommended_idx,
                }
                for i, o in enumerate(options)
            ],
            # 多选题标记：旧前端不识别该字段时安全忽略（仍按单选渲染，不解析失败）
            "multi": bool(_MULTI_CUE_RE.search(stem)),
        }
    ]
    block = json.dumps(items, ensure_ascii=False, indent=2)
    return f"{answer}\n\n```clarify\n{block}\n```"


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
        answer, sources = await _summarise_maybe_stream(
            state,
            llm,
            intent=state.get("intent") or "query",
            user_prompt=state.get("user_prompt", ""),
            plan=[],
            results=rows,
            history=_history_for_answer(state),
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
        "final_answer": _attach_clarify_from_text(answer),
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
        answer, sources = await _summarise_maybe_stream(
            state,
            llm,
            intent=state.get("intent") or "query",
            user_prompt=state.get("user_prompt", ""),
            plan=[],
            results=[],
            history=_history_for_answer(state),
        )
    except Exception as exc:
        return {
            "final_answer": _no_model_answer(exc),
            "sources": [],
            "trace": [record_trace("responder", "fail", mode="main_agent", error=str(exc))],
        }
    return {
        "final_answer": _attach_clarify_from_text(answer),
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
        answer, sources = await _summarise_maybe_stream(
            state,
            llm,
            intent=state.get("intent") or "query",
            user_prompt=user_prompt,
            plan=[],
            results=rows,
            history=_history_for_answer(state),
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
