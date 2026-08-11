"""Phase 18 自动模式决策矩阵 —— HITL 闸门的 autonomy 分支（纯函数）。

决策表（spec §4.2）：

| 风险级        | interactive        | auto                      |
|---------------|--------------------|---------------------------|
| low           | 自动放行(policy)   | 自动放行(auto_low_risk)   |
| medium/high/  | interrupt 等人     | 自动选推荐项(auto_mode)   |
| critical      |                    |                           |
| 硬阻断清单    | reject(hard_block) | reject(hard_block，不可覆盖) |

未知 risk/autonomy 值一律保守：risk 按 medium 处理、autonomy 按 interactive。
红线：本表不感知工具语义；硬阻断判定由 is_hard_blocked 单独负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

AutonomyAction = Literal["approve", "wait_user", "auto_select_recommended", "reject"]

_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
_AUTONOMY_LEVELS = frozenset({"interactive", "auto"})

# 硬阻断清单：不可逆 DDL（与 mcp-server-database 的硬阻断语义一致）
_HARD_BLOCK_PATTERN = re.compile(r"\b(drop|truncate)\b", re.IGNORECASE)
# 需要检查的 SQL 类参数键
_SQL_ARG_KEYS = ("sql", "query", "statement")


@dataclass
class AutonomyDecision:
    action: AutonomyAction
    decided_by: str


def is_hard_blocked(call: dict) -> bool:
    """检查工具调用参数是否命中硬阻断清单（DROP/TRUNCATE 等不可逆操作）。"""
    args = call.get("args") or call.get("arguments") or {}
    if not isinstance(args, dict):
        return False
    for key in _SQL_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and _HARD_BLOCK_PATTERN.search(value):
            return True
    return False


def decide(*, risk_level: str, autonomy: str, hard_blocked: bool) -> AutonomyDecision:
    """纯决策表：返回动作 + 决策者标记（写审计用）。"""
    # 硬阻断优先于一切（包括 autonomy=auto）
    if hard_blocked:
        return AutonomyDecision(action="reject", decided_by="hard_block")

    if autonomy not in _AUTONOMY_LEVELS:
        autonomy = "interactive"  # 未知值保守回退
    if risk_level not in _RISK_LEVELS:
        risk_level = "medium"  # 未知风险按中等处理

    if autonomy == "interactive":
        if risk_level == "low":
            return AutonomyDecision(action="approve", decided_by="policy")
        return AutonomyDecision(action="wait_user", decided_by="pending_user")

    # autonomy == "auto"
    if risk_level == "low":
        return AutonomyDecision(action="approve", decided_by="auto_low_risk")
    return AutonomyDecision(action="auto_select_recommended", decided_by="auto_mode")
