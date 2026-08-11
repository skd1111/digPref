"""硬规则引擎（rules.py）。

**优先级高于五维评分**（CLAUDE.md §2 红线）：
- `_LOCAL_ONLY_TASKS`（如 intent / repair）永不可被评分绕过 → 仅 local 驻留可承接
- sensitivity == PII → 仅 local / private（不 cloud）
- sensitivity == PRODUCTION → 仅 private
- budget 超限 → 剔除 cost_per_1k_tokens 高于阈值的后端

测试护栏（test_router_rules.py）：即使云端后端五维总分最高，
只要 `task_kind in _LOCAL_ONLY_TASKS`，它**不会**出现在候选里。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from agent.llm.models import (
    RESIDENCY_CLOUD,
    RESIDENCY_LOCAL,
    RESIDENCY_PRIVATE,
    LLMBackend,
    Sensitivity,
)

logger = logging.getLogger(__name__)


# CLAUDE.md §2 红线：本地敏感的 task 永远不出本机
# 与 services/agent/src/agent/llm/router.py 保持一致
_LOCAL_ONLY_TASKS = frozenset({"intent", "repair"})

# sensitivity → 允许的 data_residency
_RESIDENCY_ALLOW = {
    Sensitivity.PUBLIC: {RESIDENCY_LOCAL, RESIDENCY_PRIVATE, RESIDENCY_CLOUD},
    Sensitivity.INTERNAL: {RESIDENCY_LOCAL, RESIDENCY_PRIVATE, RESIDENCY_CLOUD},
    Sensitivity.PII: {RESIDENCY_LOCAL, RESIDENCY_PRIVATE},  # 不 cloud
    Sensitivity.PRODUCTION: {RESIDENCY_PRIVATE},  # 仅私有
}


def _violation_reason(
    backend: LLMBackend,
    task_kind: str | None,
    sensitivity: Sensitivity | None,
) -> str | None:
    """返回违规原因（None = 通过）。"""
    # 红线 1: _LOCAL_ONLY_TASKS → 仅 local 驻留可承接
    if task_kind and task_kind in _LOCAL_ONLY_TASKS:
        if backend.data_residency != RESIDENCY_LOCAL:
            return f"_LOCAL_ONLY_TASKS={task_kind} requires local residency (backend is {backend.data_residency})"

    # 红线 2: sensitivity → 限定 residency
    if sensitivity:
        allowed = _RESIDENCY_ALLOW.get(sensitivity, set())
        if backend.data_residency not in allowed:
            return f"sensitivity={sensitivity.value} not allowed residency {backend.data_residency}"

    # 通用：禁用
    if not backend.enabled:
        return f"backend {backend.name} is disabled"

    return None


def apply_hard_rules(
    candidates: Iterable[LLMBackend],
    task_kind: str | None = None,
    sensitivity: Sensitivity | None = None,
) -> list[LLMBackend]:
    """先于五维评分执行；不满足硬规则的后端直接从候选中剔除。

    Args:
        candidates: 所有可用后端
        task_kind: 任务类型（intent / plan / repair / summarise），用于 _LOCAL_ONLY_TASKS 检查
        sensitivity: 数据敏感度（PUBLIC/INTERNAL/PII/PRODUCTION）

    Returns:
        通过硬规则的候选后端列表（保持输入顺序）
    """
    passed: list[LLMBackend] = []
    for b in candidates:
        reason = _violation_reason(b, task_kind, sensitivity)
        if reason:
            logger.info("hard_rule_exclude backend=%s reason=%s", b.name, reason)
            continue
        passed.append(b)
    return passed
