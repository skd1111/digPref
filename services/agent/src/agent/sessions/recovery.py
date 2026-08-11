"""sessions.recovery —— Phase 6 V1.5 启动恢复扫描。

设计（来自 phase-6-session-mgmt.md §13 / 验收 12）：
    - app 启动时扫描：哪些 active 会话被中断（updated_at 距今 > 空闲阈值）
    - 过滤：仅根会话（非分支）+ 至少 1 条消息 + 未 archived
    - 返回 RecoveryReport：包含可恢复会话列表 + 建议下一步
    - 前端 RecoveryPanel：弹窗"检测到 N 个未完成会话，是否恢复？"

CLAUDE.md §1 HITL 不可绕过：恢复动作（打开会话 → 触发 LangGraph resume）
本身不是写操作，不需 HITL；写入的消息走 hitl_gate。
CLAUDE.md §6 物理隔离：扫描 sessions.db 单表，不跨 db。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .storage import now_ms

if TYPE_CHECKING:
    from .storage import SessionStorage


DEFAULT_IDLE_THRESHOLD_MS = 300_000  # 5 分钟


@dataclass
class RecoveryReport:
    """恢复扫描报告。

    字段：
        total: 扫描到的可恢复会话数
        resumable_ids: 会话 ID 列表（最新优先）
        oldest_idle_ms: 最久空闲时间（毫秒）
        generated_at: 报告生成时间
        threshold_ms: 触发阈值
    """

    total: int = 0
    resumable_ids: list[str] = field(default_factory=list)
    oldest_idle_ms: int = 0
    generated_at: int = 0
    threshold_ms: int = DEFAULT_IDLE_THRESHOLD_MS

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "resumable_ids": self.resumable_ids,
            "oldest_idle_ms": self.oldest_idle_ms,
            "generated_at": self.generated_at,
            "threshold_ms": self.threshold_ms,
            "needs_recovery": self.total > 0,
        }


def scan_resumable_sessions(
    storage: SessionStorage,
    *,
    idle_threshold_ms: int = DEFAULT_IDLE_THRESHOLD_MS,
    limit: int = 50,
) -> RecoveryReport:
    """扫描可恢复的会话（updated_at 距今 > 阈值 + 有消息 + 根会话）。

    Args:
        storage: SessionStorage 实例
        idle_threshold_ms: 空闲阈值（默认 5 分钟）
        limit: 最多返回条数

    Returns:
        RecoveryReport（含 needs_recovery 布尔 + resumable_ids 列表）
    """
    sessions = storage.find_resumable_sessions(
        idle_threshold_ms=idle_threshold_ms,
    )
    # 截断 limit（find_resumable_sessions 内部已 LIMIT 50）
    sessions = sessions[:limit]
    generated = now_ms()
    oldest_idle = 0
    if sessions:
        oldest_updated = min(s.updated_at for s in sessions)
        oldest_idle = max(0, generated - oldest_updated)
    return RecoveryReport(
        total=len(sessions),
        resumable_ids=[s.id for s in sessions],
        oldest_idle_ms=oldest_idle,
        generated_at=generated,
        threshold_ms=int(idle_threshold_ms),
    )


__all__ = [
    "DEFAULT_IDLE_THRESHOLD_MS",
    "RecoveryReport",
    "scan_resumable_sessions",
]
