"""DSpark 业务层配置 —— 在 shared-protocol 协议层基础上加业务默认。

字段定义 + 校验上下界统一在 `shared-protocol/dspark.py`。
本模块仅放：
    1. 业务层默认 profile 表（DEFAULT_POLICIES）
    2. 4 档预设 K + 阈值（_MODE_PARAMS）
    3. helper 函数（policy_for_mode / all_modes）
    4. 全局关闭 sentinel（SPECULATIVE_OFF）

文档：[docs/design/phase-13-dspark.md](../../../../docs/design/phase-13-dspark.md)
"""

from __future__ import annotations

from typing import get_args

from protocol.dspark import (
    DSPARK_DRAFT_P_MIN_DEFAULT_AGGRESSIVE,
    DSPARK_DRAFT_P_MIN_DEFAULT_CONSERVATIVE,
    DSPARK_DRAFT_P_MIN_DEFAULT_OFF,
    DSPARK_DRAFT_P_MIN_DEFAULT_STANDARD,
    DSPARK_N_DRAFT_MAX,
    DSPARK_N_DRAFT_MIN,
    SpeculativeMode,
    SpeculativePolicy,
)
from protocol.dspark import DSparkConfig as ProtocolDSparkConfig

# 4 档预设 K + 阈值（来自设计文档 §2.2 置信度阈值对照表）
_MODE_PARAMS: dict[str, tuple[int, float]] = {
    "aggressive": (8, DSPARK_DRAFT_P_MIN_DEFAULT_AGGRESSIVE),
    "standard": (4, DSPARK_DRAFT_P_MIN_DEFAULT_STANDARD),
    "conservative": (2, DSPARK_DRAFT_P_MIN_DEFAULT_CONSERVATIVE),
    "off": (1, DSPARK_DRAFT_P_MIN_DEFAULT_OFF),
}


def policy_for_mode(mode: SpeculativeMode) -> tuple[int, float]:
    """返回该 mode 的默认 (n_draft, draft_p_min)。"""
    return _MODE_PARAMS[mode]


def _policy(cat: str, mode: SpeculativeMode) -> SpeculativePolicy:
    """工厂：从 mode 派生 K/阈值。"""
    n_d, p_min = policy_for_mode(mode)
    return SpeculativePolicy(
        task_category=cat,
        mode=mode,
        n_draft=n_d,
        draft_p_min=p_min,
    )


# 默认 profile 表（按设计文档 §3.1 任务类型映射表）
DEFAULT_POLICIES: dict[str, SpeculativePolicy] = {
    # 敏感任务（强制关闭）
    "intent": _policy("intent", "off"),
    "repair": _policy("repair", "off"),
    "skill_router": _policy("skill_router", "off"),
    "data_summary": _policy("data_summary", "off"),
    # 复杂任务（保守）
    "plan": _policy("plan", "conservative"),
    "summarise": _policy("summarise", "conservative"),
    # 结构化任务（DSpark 加速黄金场景）
    "sql_generation": _policy("sql_generation", "aggressive"),
    "code_completion": _policy("code_completion", "aggressive"),
    # 其余默认标准
    "code_explanation": _policy("code_explanation", "standard"),
    "log_analysis": _policy("log_analysis", "standard"),
    "chat_qa": _policy("chat_qa", "conservative"),
    "toolspec": _policy("toolspec", "standard"),
}


# 全局关闭的常量（never-trustable 引用）
# 用 sentinel category "*" —— 不可能匹配任何真实 task_category，但仍 .enabled=False
SPECULATIVE_OFF = SpeculativePolicy(task_category="*", mode="off")


# === 业务层 DSparkConfig：协议层 + profiles 字段 ==============================


class DSparkConfig(ProtocolDSparkConfig):
    """业务层 DSparkConfig：协议层字段 + profiles 默认表。

    `profiles` 是业务层默认（YAML 加载失败 / 缺省时回退），不属于跨进程协议。
    deep copy DEFAULT_POLICIES —— 避免后续 cfg.profiles["x"].n_draft = 99 污染默认值。
    """

    # pydantic 构造时会深拷贝字段默认值，实例间不共享；RUF012 为误报
    profiles: dict[str, SpeculativePolicy] = dict(DEFAULT_POLICIES)  # noqa: RUF012


# 兼容 _MODE_PARAMS 单元测试
def all_modes() -> list[str]:
    return list(get_args(SpeculativeMode))


__all__ = [
    "DEFAULT_POLICIES",
    "DSPARK_N_DRAFT_MAX",
    "DSPARK_N_DRAFT_MIN",
    "SPECULATIVE_OFF",
    "DSparkConfig",
    "SpeculativeMode",
    "SpeculativePolicy",
    "all_modes",
    "policy_for_mode",
]
