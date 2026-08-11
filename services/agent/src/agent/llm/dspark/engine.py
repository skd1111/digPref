"""DSpark Engine 桩 —— V0 仅追踪决策记录，不真正调 llama.cpp。

V1 实现：
    - 加载主模型 + 草稿模型
    - 注入 llama.cpp 的 speculative_model / n_draft / draft_p_min
    - 记录加速比 / token 接受率 / 草稿长度

V0 仅做：
    - 决策被调用时记一条 metrics（供 RouterDashboard 拉取）
    - 草稿模型路径缺失 → 静默降级（不允许抛异常）
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DSparkDecisionRecord:
    """一条决策记录（最近 N 条，环形缓冲）。"""

    ts: float
    task_category: str
    speculative_enabled: bool
    n_draft: int
    draft_p_min: float
    backend: str
    reason: str  # "off-global" / "off-local-only" / "off-short" / "off-no-draft" / "applied" / "applied-default"
    max_tokens: int


@dataclass
class DSparkEngine:
    """V0 追踪层。

    真实后端（V1）会接 llama.cpp Llama() + speculative_model。
    V0 只记录决策 + 暴露给 API 读取。
    """

    _max_history: int = 200
    _records: deque[DSparkDecisionRecord] = field(init=False)
    _lock: Lock = field(default_factory=Lock)

    def __post_init__(self) -> None:
        # 把 maxlen 真正绑到 deque（让 record() 自动环形裁剪）
        self._records = deque(maxlen=self._max_history)

    def record(self, rec: DSparkDecisionRecord) -> None:
        with self._lock:
            self._records.append(rec)

    def recent(self, limit: int = 50) -> list[DSparkDecisionRecord]:
        with self._lock:
            return list(self._records)[-limit:]

    def stats(self) -> dict[str, Any]:
        """汇总最近 100 条决策：命中率 / 各类别占比。"""
        with self._lock:
            items = list(self._records)[-100:]
        if not items:
            return {
                "total_decisions": 0,
                "dspark_enabled_pct": 0.0,
                "per_category": {},
                "per_reason": {},
            }
        n = len(items)
        enabled = sum(1 for r in items if r.speculative_enabled)
        per_category: dict[str, int] = {}
        per_reason: dict[str, int] = {}
        for r in items:
            per_category[r.task_category] = per_category.get(r.task_category, 0) + 1
            per_reason[r.reason] = per_reason.get(r.reason, 0) + 1
        return {
            "total_decisions": n,
            "dspark_enabled_pct": round(enabled / n * 100, 1),
            "per_category": per_category,
            "per_reason": per_reason,
        }


# 模块级单例（lazy init；API 启动时 init）
engine = DSparkEngine()


def make_record(
    *,
    task_category: str,
    decision: Any,  # RouteDecision (避免循环 import)
    reason: str,
    max_tokens: int,
) -> DSparkDecisionRecord:
    """基于 RouteDecision 构造 record。"""
    return DSparkDecisionRecord(
        ts=time.time(),
        task_category=task_category,
        speculative_enabled=bool(getattr(decision, "speculative_enabled", False)),
        n_draft=int(getattr(decision, "n_draft", 1)),
        draft_p_min=float(getattr(decision, "draft_p_min", 1.0)),
        backend=str(getattr(decision, "backend", "unknown")),
        reason=reason,
        max_tokens=max_tokens,
    )
