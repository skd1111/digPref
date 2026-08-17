"""intent node — classify the user's last message."""

from __future__ import annotations

import time
import uuid

from agent.graph.state import AgentState, record_trace
from agent.llm.router import LMRouter
from agent.observability.cot_log import cot as cot_log
from agent.observability.trace_store import record as record_trace_persisted


async def intent_node(state: AgentState, llm: LMRouter) -> dict:
    """返回部分状态更新，由 LangGraph 合并。

    意图识别重构（2026-08-06）：优先走结构化分析 analyze_intent
    （改写句 + 细分类型 + 实体 + 追问 + 风险）；llm 替身无该方法时
    自动回退旧式 classify_intent，行为完全向后兼容。
    """
    started = time.monotonic()
    prompt = state.get("user_prompt", "")
    if not prompt and state.get("messages"):
        last = state["messages"][-1]
        prompt = getattr(last, "content", None) or (
            last.get("content") if isinstance(last, dict) else ""
        )
    run_id = state.get("run_id") or str(uuid.uuid4())
    if not prompt:
        cot_log("intent.start", run_id=run_id, prompt="", result="empty → chitchat")
        return {
            "intent": "chitchat",
            "trace": [record_trace("intent", "ok", intent="chitchat", fallback=True)],
        }
    cot_log(
        "intent.start",
        run_id=run_id,
        prompt=prompt,
        history_turns=len(state.get("messages") or []),
        llm_kind=type(llm).__name__,
    )

    # 意图向量快速路由（semantic-router 模式）：命中预置 Route 时零 LLM 直出，
    # 未命中 / embedding 不可用静默回退下方 LLM 分析（开关默认关闭）。
    analysis = None
    try:
        from agent.graph.semantic_route import get_semantic_router

        analysis = await get_semantic_router().route(prompt)
    except Exception as exc:  # 快速路径故障绝不影响主链路
        cot_log("intent.semantic_route.error", run_id=run_id, error=repr(exc))
        analysis = None
    if isinstance(analysis, dict):
        cot_log(
            "intent.semantic_route.hit",
            run_id=run_id,
            route=analysis.get("_route"),
            score=analysis.get("_route_score"),
            intent=analysis.get("intent"),
        )

    # 结构化意图分析（内部含降级链，绝不抛异常）
    if not analysis and hasattr(llm, "analyze_intent"):
        # 页面上下文（2026-08-14）：当前页签/场景注入意图分析，消除“连接”等
        # 模糊动词的歧义（如当前就在「内网模型接入配置」页签）
        from agent.graph.state import format_page_context

        page_line = format_page_context(state.get("page_context"))
        try:
            analysis = await llm.analyze_intent(
                prompt, state.get("messages"), page_context=page_line
            )
        except TypeError:
            # 注入替身不支持 page_context 参数 → 去掉参数再调（向后兼容）
            analysis = await llm.analyze_intent(prompt, state.get("messages"))
    if isinstance(analysis, dict) and analysis.get("intent"):
        intent = analysis["intent"]
        cot_log(
            "intent.analysis",
            run_id=run_id,
            intent=intent,
            intent_category=analysis.get("intent_category"),
            need_tool=analysis.get("need_tool"),
            need_clarification=analysis.get("need_clarification"),
            risk_level=analysis.get("risk_level"),
            confidence=analysis.get("confidence"),
            reason=analysis.get("reason"),
            backend=analysis.get("backend"),
            rewritten_query=analysis.get("rewritten_query"),
            entities=analysis.get("entities"),
            missing_fields=analysis.get("missing_fields"),
        )
    else:
        intent = await llm.classify_intent(prompt)
        analysis = None
        cot_log(
            "intent.classify_plain",
            run_id=run_id,
            intent=intent,
            note="结构化分析不可用，回退旧式 classify_intent",
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    await record_trace_persisted(
        "intent",
        "ok",
        run_id=run_id,
        duration_ms=duration_ms,
        summary=f"intent={intent}"
        + (f" category={analysis['intent_category']}" if analysis else ""),
    )
    # Phase 2D V1：LLM 意图分类 + 关键词回退（V0 仅关键词）
    skill_match: dict | None = None
    try:
        from agent.skills import api as skills_api

        if prompt and skills_api._loader:
            # 端侧地址以「模型管理」为准（router.db 自定义 URL/端口），与 LMRouter 同源
            from agent.llm.router import load_enabled_local_backend
            from agent.skills.router import SkillRouter

            router = SkillRouter(
                skills_api._loader,
                ollama_base_url=load_enabled_local_backend()[0],
            )
            # V1 用 async 路由（LLM 优先 + 关键词回退）；Ollama 不可用时静默降级
            routing = await router.route_async(prompt)
            if routing.skill_id:
                skill_match = {
                    "active_skill_id": routing.skill_id,
                    "active_skill_name": routing.skill_name,
                    "skill_routing": routing,
                }
    except Exception:
        # skill 模块未初始化 / LLM/keyword 都没命中 → 不影响主流程
        pass

    result: dict = {
        "intent": intent,
        "trace": [
            record_trace(
                "intent",
                "ok",
                intent=intent,
                duration_ms=duration_ms,
                structured=bool(analysis),
            )
        ],
    }
    if analysis:
        result["intent_analysis"] = analysis
        rewritten = str(analysis.get("rewritten_query") or "")
        if rewritten and rewritten != prompt:
            result["rewritten_query"] = rewritten
    if skill_match:
        result.update(skill_match)
        cot_log(
            "intent.skill_match",
            run_id=run_id,
            skill_id=skill_match.get("active_skill_id"),
            skill_name=skill_match.get("active_skill_name"),
        )
    cot_log(
        "intent.final",
        run_id=run_id,
        intent=intent,
        structured=bool(analysis),
        duration_ms=duration_ms,
    )
    return result
