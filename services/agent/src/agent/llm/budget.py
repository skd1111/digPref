"""预算控制（budget.py）。

V0 简化：
- 用户级日预算（默认 100 元 / 天；V1 改成可配）
- task_kind 临时硬预算：单次任务超 1 元 → 拒绝
- 状态：内存里维护 daily_total，V1 落 router.db.cost_daily 表

CLAUDE.md 红线：预算超限**硬拦截**（hard-stop），不让评分绕过。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from agent.llm.models import LLMBackend, BudgetVerdict
from agent.llm.metrics import emit_router_event

logger = logging.getLogger(__name__)


# 默认上限（V1 改成可配 + UI 设置）
DEFAULT_DAILY_BUDGET = 100.0       # 元
DEFAULT_PER_TASK_BUDGET = 1.0      # 元 / 单次


class BudgetController:
    """简单的日预算 + 单次预算控制。"""

    def __init__(
        self,
        daily_limit: float = DEFAULT_DAILY_BUDGET,
        per_task_limit: float = DEFAULT_PER_TASK_BUDGET,
    ):
        self._daily_limit = daily_limit
        self._per_task_limit = per_task_limit
        self._current_date = time.strftime("%Y-%m-%d")
        self._daily_spent = 0.0
        self._lock = threading.Lock()

    def estimate(self, backend: LLMBackend, estimated_tokens: int) -> float:
        """估算调用成本（元）。"""
        return (estimated_tokens / 1000.0) * backend.cost_per_1k_tokens

    def check(
        self,
        backend: LLMBackend,
        estimated_tokens: int,
    ) -> BudgetVerdict:
        """检查此次调用是否在预算内。

        Returns:
            BudgetVerdict(allowed=bool, reason=str)
        """
        with self._lock:
            # 日期跨天自动重置
            today = time.strftime("%Y-%m-%d")
            if today != self._current_date:
                self._current_date = today
                self._daily_spent = 0.0

            est = self.estimate(backend, estimated_tokens)
            if est > self._per_task_limit:
                verdict = BudgetVerdict(
                    allowed=False,
                    reason=f"per-task estimated cost {est:.4f} > limit {self._per_task_limit}",
                )
                self._emit_alert(backend, est, verdict.reason)
                return verdict
            if self._daily_spent + est > self._daily_limit:
                verdict = BudgetVerdict(
                    allowed=False,
                    reason=f"daily would exceed {self._daily_limit} (current {self._daily_spent:.2f})",
                )
                self._emit_alert(backend, est, verdict.reason)
                return verdict
            return BudgetVerdict(allowed=True, reason="ok")

    @staticmethod
    def _emit_alert(backend: LLMBackend, estimated_cost: float, reason: str) -> None:
        """预算超限时发射 llm_budget_alert SSE 事件（三处同步：stream.py / sse_bridge.rs / events.ts）。"""
        try:
            emit_router_event("llm_budget_alert", {
                "kind": "llm_budget_alert",
                "backend": backend.name,
                "estimated_cost": round(estimated_cost, 6),
                "reason": reason,
            })
        except Exception:
            pass  # best-effort，不因事件发射失败而中断路由流程

    def record_spend(self, backend: LLMBackend, actual_tokens: int) -> None:
        """记录实际花费（调用成功后）。"""
        cost = (actual_tokens / 1000.0) * backend.cost_per_1k_tokens
        with self._lock:
            self._daily_spent += cost
            logger.info(
                "budget_spend backend=%s tokens=%d cost=%.4f daily_total=%.2f",
                backend.name, actual_tokens, cost, self._daily_spent,
            )

    @property
    def daily_spent(self) -> float:
        with self._lock:
            return self._daily_spent

    @property
    def daily_limit(self) -> float:
        return self._daily_limit
