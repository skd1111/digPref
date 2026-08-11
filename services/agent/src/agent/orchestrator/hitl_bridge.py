"""orchestrator.hitl_bridge —— Phase 12 V1/V1.5 子 Agent HITL 反向 interrupt。

设计（phase-12 §1.3 + CLAUDE.md §1）：
    子 Agent 的写操作 **100% 反向**走主图那一套审批通道 —— 不重造。

V1（保留）：
    非阻塞 fail-closed —— low risk 自动 approve，其余直接 reject，
    只写 `audit.sqlite` 两条事件。适合无人值守 / 后台批处理场景。

V1.5（新增 `wait_for_user=True`）：**真 interrupt**
    1. `graph.interrupt.start_approval(approval_id, plan, timeout_sec)`
       —— 与 `hitl_gate` 完全相同的原语（Redis 优先，进程内 fallback）
    2. `events.emit_orchestrator_event("approval", …)` → `graph/stream.py` 转
       `agent://approval` → 前端 **既有** ApprovalCard 渲染（零新组件）
    3. 轮询 `check_decision()` 直到用户决策 / 超时（超时 = reject，fail-closed）
    4. `cleanup_approval()` + 审计 `sub_agent_hitl_requested` / `_decided`
       （带 correlation_id，可在决策树里回放）

红线：
    - **未经审批不许调 MCP** —— 返回 reject 时调用方必须跳过写操作
    - 超时默认 reject（绝不 fail-open）
    - `require_hitl_for_write=False`（总开关关）时才隐式批准，与 hitl_gate 语义一致
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from agent.audit.store import audit
from agent.config import settings
from agent.orchestrator import audit_bridge
from agent.orchestrator.events import EVT_APPROVAL, emit_orchestrator_event

logger = logging.getLogger(__name__)

# 轮询间隔（与 graph/interrupt.py 的 0.25s 保持同量级；略短以更早响应取消）
_POLL_INTERVAL_S = 0.05


@dataclass
class HITLRequest:
    """子 Agent 提交的 HITL 请求（写操作需审批）。"""

    request_id: str
    sub_agent_id: str
    parent_run_id: str
    operation: str  # 写操作描述（如 "UPDATE orders SET ..."）
    target: str  # 影响目标（"orders_db.orders" / "redis://..."）
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    correlation_id: str = ""
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "sub_agent_id": self.sub_agent_id,
            "parent_run_id": self.parent_run_id,
            "operation": self.operation,
            "target": self.target,
            "risk_level": self.risk_level,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
        }

    def to_plan(self) -> dict[str, Any]:
        """转成主图 `hitl_gate` 那套 plan 结构（前端 ApprovalCard 直接吃）。"""
        return {
            "server": "sub_agent",
            "tool": self.operation.split()[0].lower() if self.operation else "write",
            "args": {"operation": self.operation, "target": self.target},
            "risk_level": self.risk_level,
            "reason": f"子 Agent {self.sub_agent_id} 请求写操作审批",
            "sub_agent_id": self.sub_agent_id,
            "parent_run_id": self.parent_run_id,
            "correlation_id": self.correlation_id,
        }


@dataclass
class HITLDecision:
    """HITL 决策结果。"""

    request_id: str
    decision: Literal["approve", "reject"]
    decided_at: int
    decided_by: str = "default"
    waited_ms: int = 0
    timed_out: bool = False

    @property
    def approved(self) -> bool:
        return self.decision == "approve"


# ---- HITL Bridge -----------------------------------------------------------


class HITLBridge:
    """子 Agent → 主图 hitl_gate 的反向审批桥。

    用法（V1.5 真审批）：
        decision = await bridge.request_approval(
            sub_agent_id="sub-1", parent_run_id="run-1",
            operation="UPDATE orders SET status='paid'",
            target="orders_db.orders", risk_level="high",
            wait_for_user=True,
        )
        if decision.approved:
            ...  # 才可以调 MCP
    """

    async def request_approval(
        self,
        *,
        sub_agent_id: str,
        parent_run_id: str,
        operation: str,
        target: str,
        risk_level: Literal["low", "medium", "high", "critical"] = "medium",
        correlation_id: str = "",
        auto_approve_low_risk: bool = True,
        wait_for_user: bool = False,
        timeout_sec: int | None = None,
        parent_sub_agent_id: str | None = None,
    ) -> HITLDecision:
        """请求审批。

        Args:
            auto_approve_low_risk: low risk 免打扰（V1 语义；仅在 wait_for_user=False 时生效）
            wait_for_user: True → V1.5 真 interrupt，阻塞等前端决策（超时 reject）
            timeout_sec: 等待上限（默认 settings.approval_timeout_sec）
        """
        request_id = str(uuid.uuid4())
        request = HITLRequest(
            request_id=request_id,
            sub_agent_id=sub_agent_id,
            parent_run_id=parent_run_id,
            operation=operation,
            target=target,
            risk_level=risk_level,
            correlation_id=correlation_id or f"{parent_run_id}:{sub_agent_id}",
        )

        # 1. 写 audit（V1 兼容签名：位置参数两个，不加 kwargs）
        try:
            await audit("SUB_AGENT_HITL_REQUESTED", request.to_dict())
        except Exception as e:
            logger.warning("audit SUB_AGENT_HITL_REQUESTED failed: %s", e)
        # 1b. V1.5：结构化审计（correlation_id 可回放整棵决策树）
        await audit_bridge.log_event(
            audit_bridge.EVENT_HITL_REQUESTED,
            correlation_id=request.correlation_id,
            task_id=sub_agent_id,
            parent_task_id=parent_sub_agent_id,
            run_id=parent_run_id,
            payload={
                "approval_id": request_id,
                "operation": operation[:500],
                "target": target,
                "risk_level": risk_level,
                "wait_for_user": wait_for_user,
            },
        )

        t0 = time.monotonic()
        timed_out = False
        decided_by = "policy"

        # 2. 总开关关闭 → 隐式批准（与 hitl_gate 的 policy_disabled 分支一致）
        if not settings.require_hitl_for_write:
            decision: Literal["approve", "reject"] = "approve"
            decided_by = "policy_disabled"
        elif wait_for_user:
            # 2b. V1.5：真 interrupt —— 复用主图审批原语 + 既有 ApprovalCard
            decision, timed_out = await self._await_user_decision(
                request, timeout_sec=timeout_sec or settings.approval_timeout_sec
            )
            decided_by = "timeout" if timed_out else "user"
        elif auto_approve_low_risk and risk_level == "low":
            decision = "approve"
            decided_by = "auto_low_risk"
        else:
            # 2c. V1 语义：fail-closed，防止意外副作用
            decision = "reject"
            decided_by = "fail_closed"

        result = HITLDecision(
            request_id=request_id,
            decision=decision,
            decided_at=int(time.time() * 1000),
            decided_by=decided_by,
            waited_ms=int((time.monotonic() - t0) * 1000),
            timed_out=timed_out,
        )

        # 3. 写 audit（decision；V1 兼容签名）
        try:
            await audit(
                "SUB_AGENT_HITL_DECIDED",
                {
                    **request.to_dict(),
                    "decision": result.decision,
                    "decided_at": result.decided_at,
                    "decided_by": result.decided_by,
                    "waited_ms": result.waited_ms,
                    "timed_out": result.timed_out,
                },
            )
        except Exception as e:
            logger.warning("audit SUB_AGENT_HITL_DECIDED failed: %s", e)
        await audit_bridge.log_event(
            audit_bridge.EVENT_HITL_DECIDED,
            correlation_id=request.correlation_id,
            task_id=sub_agent_id,
            parent_task_id=parent_sub_agent_id,
            run_id=parent_run_id,
            payload={
                "approval_id": request_id,
                "decision": result.decision,
                "decided_by": result.decided_by,
                "waited_ms": result.waited_ms,
                "timed_out": result.timed_out,
                "risk_level": risk_level,
            },
        )
        return result

    # ---- 内部：真 interrupt ----------------------------------------------

    async def _await_user_decision(
        self,
        request: HITLRequest,
        *,
        timeout_sec: int,
    ) -> tuple[Literal["approve", "reject"], bool]:
        """发起审批 + 推 SSE + 轮询决策。返回 (decision, timed_out)。"""
        from agent.graph.interrupt import (
            check_decision,
            cleanup_approval,
            start_approval,
        )

        approval_id = request.request_id
        plan = request.to_plan()
        try:
            await start_approval(
                approval_id=approval_id,
                plan=plan,
                timeout_sec=timeout_sec,
            )
        except Exception as exc:
            logger.warning("[hitl_bridge] start_approval 失败 → fail-closed: %s", exc)
            return "reject", False

        # 推 SSE：复用主图 approval 通道（前端 ApprovalCard 零改动）
        emit_orchestrator_event(
            EVT_APPROVAL,
            {
                "kind": "approval",
                "approval_id": approval_id,
                "plan": plan,
                "sub_agent_id": request.sub_agent_id,
                "parent_run_id": request.parent_run_id,
                "correlation_id": request.correlation_id,
                "risk_level": request.risk_level,
                "source": "sub_agent",
            },
        )

        deadline = time.monotonic() + timeout_sec
        decision: str | None = None
        try:
            while time.monotonic() < deadline:
                decision = await check_decision(approval_id)
                if decision:
                    break
                await asyncio.sleep(_POLL_INTERVAL_S)
        except asyncio.CancelledError:
            await self._safe_cleanup(cleanup_approval, approval_id)
            raise
        await self._safe_cleanup(cleanup_approval, approval_id)

        if decision == "approve":
            return "approve", False
        if decision == "reject":
            return "reject", False
        logger.warning(
            "[hitl_bridge] 审批超时（%ds）→ 自动拒绝 sub=%s", timeout_sec, request.sub_agent_id
        )
        return "reject", True

    @staticmethod
    async def _safe_cleanup(cleanup_fn, approval_id: str) -> None:
        try:
            await cleanup_fn(approval_id)
        except Exception:
            pass


# ---- 全局单例 -------------------------------------------------------------

_default_bridge: HITLBridge | None = None


def get_default_hitl_bridge() -> HITLBridge:
    global _default_bridge
    if _default_bridge is None:
        _default_bridge = HITLBridge()
    return _default_bridge


def reset_default_hitl_bridge() -> None:
    """测试 hook。"""
    global _default_bridge
    _default_bridge = None


__all__ = [
    "HITLBridge",
    "HITLDecision",
    "HITLRequest",
    "get_default_hitl_bridge",
    "reset_default_hitl_bridge",
]
