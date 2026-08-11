"""reqflow —— 运营模式需求改造工作流（需求卡片 V1）。

业务人员基于业务功能点与 AI 对齐改造需求 → 结构化需求卡片 →
批次管理/状态流转 → 导出需求文档（MD/DOCX）。审批模块 V1 不实现，
状态手动切换，can_transition() 留接缝。

公开 API：
- models：ReqBatch / ReqCard / 状态常量 / can_transition
- storage：ReqCardStorage（reqcards.db）
- api：FastAPI router（/reqflow 前缀）
"""

from __future__ import annotations

from agent.reqflow.models import (
    ALL_STATUSES,
    APPROVED,
    DEVELOPING,
    DONE,
    DRAFT,
    PENDING_APPROVAL,
    REJECTED,
    STATUS_LABEL,
    ReqBatch,
    ReqCard,
    can_transition,
)

__all__ = [
    "ALL_STATUSES",
    "APPROVED",
    "DEVELOPING",
    "DONE",
    "DRAFT",
    "PENDING_APPROVAL",
    "REJECTED",
    "STATUS_LABEL",
    "ReqBatch",
    "ReqCard",
    "can_transition",
]
