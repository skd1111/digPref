"""intent node — classify the user's last message."""
from __future__ import annotations

import time
import uuid

from agent.graph.state import AgentState, record_trace
from agent.llm.router import LMRouter
from agent.observability.trace_store import record as record_trace_persisted


async def intent_node(state: AgentState, llm: LMRouter) -> dict:
    """返回部分状态更新，由 LangGraph 合并。"""
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

    intent = await llm.classify_intent(prompt)
    duration_ms = int((time.monotonic() - started) * 1000)
    # 使用 state 中已有的 run_id，不存在则生成新 UUID
    run_id = state.get("run_id") or str(uuid.uuid4())
    await record_trace_persisted(
        "intent", "ok",
        run_id=run_id,
        duration_ms=duration_ms,
        summary=f"intent={intent}",
    )
    # Phase 2D V1：LLM 意图分类 + 关键词回退（V0 仅关键词）
    skill_match: dict | None = None
    try:
        from agent.skills import api as skills_api
        if prompt and skills_api._loader:
            from agent.skills.router import SkillRouter
            router = SkillRouter(skills_api._loader)
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
        "trace": [record_trace(
            "intent", "ok",
            intent=intent,
            duration_ms=duration_ms,
        )],
    }
    if skill_match:
        result.update(skill_match)
    return result