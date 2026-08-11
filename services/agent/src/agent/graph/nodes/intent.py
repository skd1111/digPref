"""intent node — classify the user's last message."""

from __future__ import annotations

import time
import uuid

from agent.graph.state import AgentState, record_trace
from agent.llm.router import LMRouter
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
    if not prompt:
        return {
            "intent": "chitchat",
            "trace": [record_trace("intent", "ok", intent="chitchat", fallback=True)],
        }

    # 意图向量快速路由（semantic-router 模式）：命中预置 Route 时零 LLM 直出，
    # 未命中 / embedding 不可用静默回退下方 LLM 分析（开关默认关闭）。
    analysis = None
    try:
        from agent.graph.semantic_route import get_semantic_router

        analysis = await get_semantic_router().route(prompt)
    except Exception:  # 快速路径故障绝不影响主链路
        analysis = None

    # 结构化意图分析（内部含降级链，绝不抛异常）
    if not analysis and hasattr(llm, "analyze_intent"):
        analysis = await llm.analyze_intent(prompt, state.get("messages"))
    if isinstance(analysis, dict) and analysis.get("intent"):
        intent = analysis["intent"]
    else:
        intent = await llm.classify_intent(prompt)
        analysis = None
    duration_ms = int((time.monotonic() - started) * 1000)
    # 使用 state 中已有的 run_id，不存在则生成新 UUID
    run_id = state.get("run_id") or str(uuid.uuid4())
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
    return result
