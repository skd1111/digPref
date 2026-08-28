"""hitl_gate 节点 —— 在写操作之前将图暂停，等待用户审批。

实现方式（借鉴 OpenAI Codex CLI 的审批模式）：
    1. 首次进入：生成 approval_id，启动后台轮询任务，立即返回
       awaiting_approval=True → SSE 适配器向前端发出审批卡片
    2. 后续进入（由 edges 路由回来）：调用 check_decision() 查看结果
       - 已决定 → 返回 decision，清除 awaiting_approval
       - 未决定 → 继续等待（返回 awaiting_approval=True）

Phase 18：首次进入先过自主性决策矩阵（dual/autonomy）：
    - 硬阻断清单（DROP/TRUNCATE）→ 直接 reject，autonomy=auto 也不可覆盖
    - autonomy=auto 且 medium+ → 按推荐选项自动执行（fail-closed：无有效推荐项则等人）
    - 其余 → 原有 interrupt 等人流程

这种非阻塞设计确保：
    - 前端能及时收到审批事件并渲染 UI
    - async worker 不被长时间阻塞
    - Agent 重启后可恢复（Redis 持久化）
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from agent.audit.store import audit
from agent.config import settings
from agent.dual.autonomy import AutonomyDecision, decide, is_hard_blocked
from agent.graph.exemptions import add_exempt, exemption_scope, is_exempt, tool_kind_key
from agent.graph.interrupt import check_decision, cleanup_approval, start_approval
from agent.graph.state import AgentState, next_step, record_trace
from agent.safety.write_detector import is_write_call

logger = logging.getLogger(__name__)

# 等待审批时的图循环节流（BUGFIX #138）：route_after_hitl 在 awaiting_approval 时
# 把图路由回本节点，此前等待分支无任何 sleep → 约 130 次/秒空转（实测 1 秒 200+
# 条相同思维链、一次审批刷出 1300+ 步）。与 interrupt 后台轮询的 0.25s 对齐：
# 决策延迟无感知（后台任务 0.25s 才把决策写进内存缓存），空转降三个量级。
_WAIT_POLL_SEC = 0.25


async def hitl_gate_node(state: AgentState, llm: Any | None = None) -> dict:
    call = next_step(state)
    if call is None:
        return {"trace": [record_trace("hitl_gate", "skipped", reason="no_call")]}

    # builtin 工具：以 registry 风险等级为准（LLM 计划可能未带 risk_level）
    risk_level = call.get("risk_level", "read")
    if call.get("server") == "builtin":
        try:
            from agent.builtin.registry import TOOL_RISK_LEVEL

            risk_level = TOOL_RISK_LEVEL.get(call.get("name", ""), risk_level)
        except Exception:
            pass

    # 非写操作 + 风险等级为 read → 跳过 HITL
    if not is_write_call(call) and risk_level == "read":
        return {
            "trace": [record_trace("hitl_gate", "skipped", reason="read_only")],
            "approval_id": None,
            "approval_decision": None,
            # 显式清掉 awaiting_approval —— 否则 dispatcher 前置闸门留下的
            # awaiting_approval=True 会让 route_after_hitl 死循环
            "awaiting_approval": False,
        }

    # 总开关关闭 → 隐式批准
    if not settings.require_hitl_for_write:
        return {
            "approval_id": None,
            "approval_decision": "approve",
            "trace": [record_trace("hitl_gate", "skipped", reason="policy_disabled")],
        }

    # ---- 检查是否已有进行中的审批 ----
    existing_id = state.get("approval_id")
    if existing_id:
        # 这是后续进入（由 edges 路由回来检查决策）
        decision = await check_decision(existing_id)
        if decision:
            # 「此后都按此执行」（2026-08-25）：批准 + 登记本会话同类豁免；
            # 出口归一为 approve，下游（dispatcher/catalog/loop）无需感知新值。
            # 硬阻断操作不会走到这里（前端不提供该按钮，且首次进入先过 is_hard_blocked）。
            if decision == "approve_always":
                scope, kind = exemption_scope(state), tool_kind_key(call)
                add_exempt(scope, kind)
                await _audit_autonomy(
                    state, call,
                    AutonomyDecision(action="approve", decided_by="session_exempt"),
                    reason=f"用户选择「此后都按此执行」：{kind}（scope={scope}）",
                )
                decision = "approve"
            # 决策已到达 → 清理并返回
            await cleanup_approval(existing_id)
            return {
                # **关键**：清空 approval_id —— 否则下次再进入时仍带这个过期 UUID，
                # check_decision() 返回 None（数据已被清理）→ 落到"尚未决定"分支 → 无限循环
                "approval_id": None,
                "approval_decision": decision,
                "awaiting_approval": False,
                "approval_started_at": None,
                "approval_options": None,  # Phase 18：决策后清理选项
                "trace": [
                    record_trace(
                        "hitl_gate",
                        "ok",
                        approval_id=existing_id,
                        decision=decision,
                        risk_level=call.get("risk_level"),
                    )
                ],
            }
        # 尚未决定 → gate 侧超时守卫（fail-closed）：后台轮询任务正常会在超时时
        # 写入 reject；但 Agent 重启 / 任务丢失时无人写决策，不能无限等待
        started = state.get("approval_started_at")
        if started is None:
            # 存量进行中的审批无时间戳 → 从当前时刻起补记，下轮开始守卫。
            # 注意：稳态等待不回写 awaiting_approval —— 状态合并保留原值 True，
            # 路由不受影响，但节点输出不再携带该字段 → 思维链不再每次重入都
            # 刷一条相同的「已发起审批请求」（BUGFIX #139，配合 #138 节流）。
            await asyncio.sleep(_WAIT_POLL_SEC)  # 节流，防图循环空转（BUGFIX #138）
            return {
                "approval_started_at": time.time(),
                # 不记 trace：每次重入都记会在思维链刷出大量相同条目（BUGFIX #139）
            }
        if time.time() - float(started) >= settings.approval_timeout_sec:
            await cleanup_approval(existing_id)
            logger.warning("审批 %s gate 侧超时（无决策到达）→ 自动拒绝", existing_id)
            return {
                "approval_id": None,
                "approval_decision": "reject",
                "awaiting_approval": False,
                "approval_started_at": None,
                "approval_options": None,
                "trace": [
                    record_trace(
                        "hitl_gate", "fail", reason="timeout_guard", approval_id=existing_id
                    )
                ],
            }
        await asyncio.sleep(_WAIT_POLL_SEC)  # 节流，防图循环空转（BUGFIX #138）
        # 稳态等待不产出任何输出字段：状态合并保留 awaiting_approval=True 照常路由，
        # collector 无内容可记 → 思维链不再每次重入刷一条相同条目（BUGFIX #139）
        return {}

    # ---- 首次进入：发起审批 ----
    approval_id = str(uuid.uuid4())

    # Phase 18：硬阻断检测 + 自主性决策矩阵（硬阻断永远优先于会话豁免）
    blocked = is_hard_blocked(call)

    # 会话豁免（2026-08-25）：用户在本会话已对该工具类选过「此后都按此执行」
    # → 自动放行（审计留痕）；硬阻断不受豁免影响。
    if not blocked:
        scope, kind = exemption_scope(state), tool_kind_key(call)
        if is_exempt(scope, kind):
            await _audit_autonomy(
                state, call,
                AutonomyDecision(action="approve", decided_by="session_exempt"),
                reason=f"会话豁免命中：{kind}（scope={scope}）",
            )
            return {
                "approval_id": None,
                "approval_decision": "approve",
                "awaiting_approval": False,
                "trace": [
                    record_trace(
                        "hitl_gate", "ok", reason="session_exempt",
                        risk_level=risk_level, kind=kind,
                    )
                ],
            }

    verdict = decide(
        risk_level=risk_level,
        autonomy=state.get("autonomy") or "interactive",
        hard_blocked=blocked,
    )

    if verdict.action == "reject":
        # 硬阻断：即使自动模式也直接拒绝（不可逆操作红线）
        await _audit_autonomy(state, call, verdict, reason="硬阻断清单：不可逆操作")
        return {
            "approval_id": None,
            "approval_decision": "reject",
            "awaiting_approval": False,
            "trace": [
                record_trace(
                    "hitl_gate",
                    "fail",
                    reason="hard_block",
                    risk_level=risk_level,
                )
            ],
        }

    if verdict.action == "approve":
        # low 风险自动放行（policy / auto_low_risk）
        await _audit_autonomy(state, call, verdict)
        return {
            "approval_id": None,
            "approval_decision": "approve",
            "awaiting_approval": False,
            "trace": [
                record_trace(
                    "hitl_gate",
                    "ok",
                    reason=f"auto_approved:{verdict.decided_by}",
                    risk_level=risk_level,
                )
            ],
        }

    # Phase 18：medium+ 风险审批生成推荐选项（失败回退二元审批，不阻塞）
    approval_options: dict | None = None
    if risk_level in ("medium", "high", "critical") and llm is not None:
        from agent.dual.options import generate_approval_options

        options, rec, reason = await generate_approval_options(llm, call)
        if options:
            approval_options = {
                "options": [o.model_dump(by_alias=True) for o in options],
                "recommendedOptionId": rec,
                "recommendationReason": reason,
            }

    if verdict.action == "auto_select_recommended":
        # 自动模式：有有效推荐项（非"不执行"）→ 自动批准；否则 fail-closed 等人
        opts = (approval_options or {}).get("options") or []
        rec_id = (approval_options or {}).get("recommendedOptionId")
        rec_opt = next((o for o in opts if o.get("id") == rec_id), None)
        if rec_opt is not None and rec_opt.get("label") != "不执行":
            await _audit_autonomy(
                state,
                call,
                verdict,
                reason=(approval_options or {}).get("recommendationReason"),
                option_label=rec_opt.get("label"),
            )
            return {
                "approval_id": None,
                "approval_decision": "approve",
                "awaiting_approval": False,
                "approval_options": approval_options,
                "trace": [
                    record_trace(
                        "hitl_gate",
                        "ok",
                        reason="auto_mode:recommended",
                        risk_level=risk_level,
                        option=rec_opt.get("label"),
                    )
                ],
            }
        logger.warning("auto mode without valid recommended option → fail-closed to user approval")

    try:
        await start_approval(
            approval_id=approval_id,
            plan=call,
            timeout_sec=settings.approval_timeout_sec,
        )
    except Exception as exc:
        # 发起审批失败 → fail-closed 直接拒绝（绝不让写操作在审批缺失时放行）
        logger.error("start_approval 失败 → fail-closed reject: %s", exc)
        return {
            "approval_id": None,
            "approval_decision": "reject",
            "awaiting_approval": False,
            "approval_started_at": None,
            "trace": [
                record_trace("hitl_gate", "fail", reason="start_failed", approval_id=approval_id)
            ],
        }

    return {
        "approval_id": approval_id,
        "awaiting_approval": True,
        "approval_started_at": time.time(),
        "approval_options": approval_options,
        "trace": [
            record_trace(
                "hitl_gate",
                "running",
                reason="requested",
                approval_id=approval_id,
                risk_level=call.get("risk_level"),
                has_options=approval_options is not None,
            )
        ],
    }


async def _audit_autonomy(
    state: AgentState,
    call: dict,
    verdict: AutonomyDecision,
    *,
    reason: str | None = None,
    option_label: str | None = None,
) -> None:
    """Phase 18：自动决策全量审计（best-effort，不阻塞主流程）。"""
    try:
        await audit(
            "AUTO_MODE_DECISION",
            {
                "tool": call.get("name"),
                "server": call.get("server"),
                "risk_level": call.get("risk_level"),
                "action": verdict.action,
                "decided_by": verdict.decided_by,
                "autonomy": state.get("autonomy"),
                "work_mode": state.get("work_mode"),
                "recommendation_reason": reason,
                "selected_option": option_label,
            },
            run_id=state.get("run_id"),
        )
    except Exception as exc:
        logger.warning("audit AUTO_MODE_DECISION failed: %s", exc)
