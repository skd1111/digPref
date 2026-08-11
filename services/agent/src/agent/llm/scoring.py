"""五维评分（scoring.py）。

V0 实现：每个后端根据 task_category + sensitivity + 当前 LLM 状态，
产出 5 维 0-1 分数（capability / cost / latency / compliance / availability）。
ScoreBreakdown.total 是 5 维加权和（权重 hardcode 0.35/0.25/0.20/0.15/0.05，
V1 改成可配置权重 + 用户面板可调）。

CLAUDE.md 红线：评分仅用于选择后端；**不能**推翻硬规则。
"""

from __future__ import annotations

import logging

from agent.llm.models import LLMBackend, ScoreBreakdown, TaskCategory

logger = logging.getLogger(__name__)


def _capability_score(backend: LLMBackend, category: TaskCategory) -> float:
    """能力分：根据后端 capabilities 与 task_category 匹配程度。

    V0 简化：根据后端类型 + category 简单映射。
    V1 升级：从 LLM benchmark 历史数据加权。
    """
    set(backend.capabilities or [])
    # 简化：local 后端适合 SIMPLE，private 适合 MEDIUM，cloud 适合 COMPLEX
    if backend.type == "local" and category == TaskCategory.SIMPLE:
        return 0.9
    if backend.type == "private" and category in (TaskCategory.MEDIUM, TaskCategory.SIMPLE):
        return 0.85
    if backend.type == "cloud" and category == TaskCategory.COMPLEX:
        return 0.95
    if backend.type == "cloud" and category == TaskCategory.MEDIUM:
        return 0.7
    if backend.type == "private" and category == TaskCategory.COMPLEX:
        return 0.6
    if backend.type == "local" and category == TaskCategory.COMPLEX:
        return 0.3
    return 0.5  # 兜底


def _cost_score(backend: LLMBackend) -> float:
    """成本分：cost 越低分越高。V0 简单线性。"""
    c = backend.cost_per_1k_tokens
    if c <= 0:
        return 1.0  # 本地或免费
    if c >= 0.1:
        return 0.0
    # 0 ~ 0.1 线性映射
    return max(0.0, 1.0 - c / 0.1)


def _latency_score(backend: LLMBackend) -> float:
    """延迟分：timeout 越短分越高。V0 简单线性。"""
    t = backend.timeout_seconds
    if t <= 5:
        return 1.0
    if t >= 60:
        return 0.0
    return max(0.0, 1.0 - (t - 5) / 55)


def _compliance_score(backend: LLMBackend) -> float:
    """合规分：data_residency 越严分越高。V0 简单映射。"""
    return {
        "local": 1.0,  # 完全本地，最高
        "private": 0.85,  # 私有化部署
        "cloud": 0.4,  # 云端，最低
    }.get(backend.data_residency, 0.5)


def _availability_score(backend: LLMBackend, failure_count: int) -> float:
    """可用性分：failure_count 越少分越高。V0 简单倒数。"""
    if backend.type == "local":
        base = 0.9  # 本地一般不会挂
    elif backend.type == "private":
        base = 0.8
    else:
        base = 0.95  # 云端 SLA 一般高
    # 每次失败扣 0.1，最低 0.0
    return max(0.0, base - 0.1 * failure_count)


def score_backend(
    backend: LLMBackend,
    category: TaskCategory,
    failure_count: int = 0,
) -> ScoreBreakdown:
    """计算一个后端的五维评分。

    Args:
        backend: LLM 后端配置
        category: 任务分类
        failure_count: 该后端最近的失败次数（用于 availability 维度）

    Returns:
        ScoreBreakdown（5 维 0-1 + 加权和 total）
    """
    s = ScoreBreakdown(
        capability=_capability_score(backend, category),
        cost=_cost_score(backend),
        latency=_latency_score(backend),
        compliance=_compliance_score(backend),
        availability=_availability_score(backend, failure_count),
    )
    logger.debug(
        "score_backend %s category=%s total=%.3f",
        backend.name,
        category.value,
        s.total,
    )
    return s
