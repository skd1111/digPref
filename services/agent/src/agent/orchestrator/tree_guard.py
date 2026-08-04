"""Phase 12 — 派生树硬上限（铁律 2）。

设计文档强制约束（CLAUDE.md 设计原则 §10 / docs/design/phase-12 §1.2）：
  - max_depth = 2（主 Agent = 0，一级子 = 1，二级子 = 2；上限 = 2 即不能再派）
  - total_nodes ≤ 30

不做白名单放宽。这条约束在 spec.py 也已经留好位，但实际校验在这里完成。
"""
from __future__ import annotations

from agent.orchestrator.spec import SubAgentSpec


# 硬上限（V0 锁死，不读 config —— 设计文档明确禁止）
MAX_DEPTH = 2
MAX_TOTAL_NODES = 30


class TreeLimitExceeded(ValueError):
    """派生树超过硬上限（max_depth / total_nodes）。"""

    def __init__(self, reason: str, current: int, limit: int) -> None:
        super().__init__(
            f"派生树硬上限触发：{reason} 当前={current} 上限={limit}"
        )
        self.reason = reason
        self.current = current
        self.limit = limit


def enforce_tree_limits(spec: SubAgentSpec, current_total_nodes: int) -> None:
    """在 spawn 前校验 spec 是否合法。

    Args:
        spec: 要派生的子 Agent 规格。
        current_total_nodes: 当前进程内已派生的总节点数（含主 Agent 自己？不算，仅子 Agent）。

    Raises:
        TreeLimitExceeded: 派生深度超 max_depth 或总数超 MAX_TOTAL_NODES。
    """
    if spec.depth < 1:
        raise TreeLimitExceeded("depth<1", spec.depth, 1)
    if spec.depth > MAX_DEPTH:
        raise TreeLimitExceeded("depth 超 max_depth", spec.depth, MAX_DEPTH)
    # 含本次派生后的总数
    next_total = current_total_nodes + 1
    if next_total > MAX_TOTAL_NODES:
        raise TreeLimitExceeded("total_nodes 超 max", next_total, MAX_TOTAL_NODES)