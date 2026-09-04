"""intent node — classify the user's last message."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from agent.graph.state import AgentState, record_trace
from agent.llm.router import LMRouter
from agent.observability.cot_log import cot as cot_log
from agent.observability.trace_store import record as record_trace_persisted

if TYPE_CHECKING:
    from agent.skills.models import SkillRoutingResult

# Skill 粘性（2026-08-26）：追问/修改类短句的线索词 —— 命中且输入不长时继承上一轮 skill
_FOLLOWUP_CUES = (
    "太丑",
    "不好看",
    "重新",
    "重做",
    "再来",
    "优化",
    "美化",
    "调整",
    "修改",
    "改一下",
    "改成",
    "换",
    "不对",
    "不行",
    "不满意",
    "继续",
    "接着",
    "加上",
    "补充",
    "删掉",
    "去掉",
    "这份",
    "这个",
    "刚才",
    "上面",
    "上一个",
)
# 粘性生效的输入长度上限：长输入大概率是新任务，不继承旧 skill（关键词层会重新判）
_FOLLOWUP_MAX_LEN = 120

# 成功路由回写的 fire-and-forget 任务集（防 GC；RUF006）
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_background(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def _looks_like_followup(prompt: str) -> bool:
    """短句 + 修改/反馈线索词 → 判为对上一轮产物的追问。"""
    p = prompt.strip()
    if not p or len(p) > _FOLLOWUP_MAX_LEN:
        return False
    return any(cue in p for cue in _FOLLOWUP_CUES)


def _inherit_last_skill(prompt: str, last_skill_id: str) -> SkillRoutingResult | None:
    """构造继承的 SkillRoutingResult；不满足条件返 None（失败静默，不抛异常）。"""
    sid = last_skill_id.strip()
    if not sid or not _looks_like_followup(prompt):
        return None
    try:
        from agent.skills import api as skills_api
        from agent.skills.models import SkillRoutingResult

        last = skills_api._loader.get(sid) if skills_api._loader else None
        if not last or not last.enabled:
            return None
        return SkillRoutingResult(
            skill_id=sid,
            skill_name=last.name,
            confidence=0.5,
            matched_keywords=[],
        )
    except Exception:
        return None


def _load_pinned_skill(skill_id: str) -> SkillRoutingResult | None:
    """Skill 强钉（2026-08-28）：直接装载用户经 `/` 指令指定的 skill。

    与自动路由的区别：不走 LLM 分类也不走关键词（省一次模型调用），
    优先级最高；目标不存在/已停用返 None → 回退正常路由（不阻断对话）。
    """
    sid = skill_id.strip()
    if not sid:
        return None
    try:
        from agent.skills import api as skills_api
        from agent.skills.models import SkillRoutingResult

        sk = skills_api._loader.get(sid) if skills_api._loader else None
        if not sk or not sk.enabled:
            return None
        return SkillRoutingResult(
            skill_id=sid,
            skill_name=sk.name,
            confidence=1.0,
            matched_keywords=[],
        )
    except Exception:
        return None


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
        # 操作链路短期记忆（2026-08-31）：追问/修改类短句注入同任务页签的
        # 近期意图链路，让「换个参数再跑一次」继承上一轮的意图框架。
        if _looks_like_followup(prompt):
            try:
                from agent.graph.intent_memory import recent_chain

                chain = await recent_chain(str(state.get("task_id") or "default"))
                if chain:
                    page_line = (
                        (page_line + "；" if page_line else "")
                        + "前几轮操作："
                        + " → ".join(chain[-3:])
                    )
            except Exception:  # 记忆读取故障不影响分析，静默跳过
                pass
        try:
            analysis = await llm.analyze_intent(
                prompt, state.get("messages"), page_context=page_line
            )
        except TypeError:
            # 注入替身不支持 page_context 参数 → 去掉参数再调（向后兼容）
            analysis = await llm.analyze_intent(prompt, state.get("messages"))

    # 结构化槽位校验（2026-08-31）：必填实体缺失且高风险 → 代码层强制追问，
    # 绝不放行猜测执行；语义路由直出（_route 标记）为预置结果跳过。
    if isinstance(analysis, dict) and "_route" not in analysis:
        try:
            from agent.llm.intent_slots import validate_slots

            analysis = validate_slots(analysis)
        except Exception as exc:  # 校验故障不影响主链路，回退原结果
            cot_log("intent.slot_guard.error", run_id=run_id, error=repr(exc))

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
        # 成功路由回写（2026-08-31）：Few-Shot 案例库 + 操作链路记忆（fire-and-
        # forget；只存实体键名，参数明文不落库）。语义路由直出（_route）与
        # plain/mock 降级结果不入库。
        if "_route" not in analysis and str(analysis.get("backend") or "") not in (
            "plain",
            "empty",
            "mock",
            "",
        ):
            try:
                from agent.graph.intent_memory import (
                    record_example,
                    record_recent,
                    summarize_analysis,
                )

                raw_entities = analysis.get("entities")
                entity_keys = list(raw_entities.keys()) if isinstance(raw_entities, dict) else []
                _spawn_background(
                    record_example(
                        run_id,
                        str(analysis.get("rewritten_query") or prompt),
                        str(analysis.get("intent_category") or ""),
                        entity_keys,
                    )
                )
                _spawn_background(
                    record_recent(
                        str(state.get("task_id") or "default"), summarize_analysis(analysis)
                    )
                )
            except Exception:  # 回写故障绝不影响主链路
                pass
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
    # Skill 路由显形（2026-09-01）：本段在上方 duration_ms 截表之后执行，此前完全
    # 无 trace，探活 + 最多两轮 Ollama 分类的最坏 ~11s 是隐形黑洞；单独计时并
    # 落 skill_route 条目。
    skill_route_started = time.monotonic()
    skill_route_status = "skipped"
    skill_route_extra: dict = {}
    try:
        from agent.skills import api as skills_api

        if prompt and skills_api._loader:
            # Skill 强钉（2026-08-28）：用户经 `/` 指令手动指定时直接短路路由器，
            # 关键词/LLM 分类/粘性继承全部跳过——低优先级命中物理不进入输入空间，
            # 本地小模型与云端大模型同一策略（省 token + 免规则互掐）
            pinned_routing = _load_pinned_skill(str(state.get("pinned_skill_id") or ""))
            if pinned_routing:
                routing = pinned_routing
                skill_route_status = "ok"
                skill_route_extra = {"mode": "pinned"}
            # 快路径短路（2026-09-01）：语义路由直出是零 LLM 预置场景，闲聊走罐头模板，
            # 都不需要 Skill 规范注入——跳过 SkillRouter 探活 + 最多两轮 Ollama 分类（最坏 ~11s）。
            # 强钉是用户显式动作，优先级最高，不在此短路范围（见上分支）。
            elif (isinstance(analysis, dict) and "_route" in analysis) or intent == "chitchat":
                routing = None
                skill_route_extra = {
                    "mode": "fast_path",
                    "reason": (
                        "semantic_route_hit"
                        if isinstance(analysis, dict) and "_route" in analysis
                        else "chitchat"
                    ),
                }
                cot_log("intent.skill_route.skip", run_id=run_id, **skill_route_extra)
            else:
                # 端侧地址以「模型管理」为准（router.db 自定义 URL/端口），与 LMRouter 同源
                from agent.llm.router import load_enabled_local_backend
                from agent.skills.router import SkillRouter

                router = SkillRouter(
                    skills_api._loader,
                    ollama_base_url=load_enabled_local_backend()[0],
                )
                # V1 用 async 路由（LLM 优先 + 关键词回退）；Ollama 不可用时静默降级
                routing = await router.route_async(prompt)
                if not routing.skill_id:
                    # Skill 粘性（2026-08-26）：本轮未命中新 skill，但前端透传了上一轮
                    # 命中的 skill 且本轮是追问/修改类短句（如「太丑了，用 skill 优化」）
                    # → 继承上一轮 skill，避免脱离设计规范的裸生成。
                    inherited = _inherit_last_skill(prompt, str(state.get("last_skill_id") or ""))
                    if inherited:
                        routing = inherited
                skill_route_status = "ok"
                skill_route_extra = {"mode": "routed"}
            if routing is not None and routing.skill_id:
                skill_match = {
                    "active_skill_id": routing.skill_id,
                    "active_skill_name": routing.skill_name,
                    "skill_routing": routing,
                }
    except Exception:
        # skill 模块未初始化 / LLM/keyword 都没命中 → 不影响主流程
        skill_route_status = "fail"
    skill_route_ms = int((time.monotonic() - skill_route_started) * 1000)

    result: dict = {
        "intent": intent,
        "trace": [
            record_trace(
                "intent",
                "ok",
                intent=intent,
                duration_ms=duration_ms,
                structured=bool(analysis),
            ),
            record_trace(
                "skill_route",
                skill_route_status,
                duration_ms=skill_route_ms,
                **skill_route_extra,
            ),
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
