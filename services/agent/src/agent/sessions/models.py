"""sessions.models —— Phase 6 V0 + V1.5 会话数据模型。

设计（来自 phase-6-session-mgmt.md §3.1）：
    - Session：会话元数据（id / title / owner / project_name / created_at / updated_at）
    - Message：会话内一条消息（role: user/assistant/system/tool；content；created_at）
    - SessionCheckpoint：LangGraph 状态快照引用（thread_id / checkpoint_id / metadata）
    - SessionStatus：active / archived / deleted

Phase 6 V1.5 扩展（CLAUDE.md §6 物理隔离 sessions.db）：
    - Session 新字段：parent_session_id / branch_from_checkpoint_id / branch_label
      （分支）/ share_tokens_json / permissions_json / shared_at（共享权限）
    - SessionEvent：会话级事件哈希链（SHA-256 链式，防篡改，与 audit_expert 同等级）
    - ShareToken：会话分享令牌（permission / created_at / expires_at）

V1.5 仍在 V0 数据类上扩展：
    - 加密 .eas 导出（PII 脱敏 + Fernet + Keyring） —— 在 export.py
    - SessionEvent 链式哈希防篡改（与 Phase 5 审计同等级） —— 在 storage.py
    - 分支（parent_session_id + branch_from_checkpoint_id） —— 在 storage.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SessionStatus = Literal["active", "archived", "deleted"]
MessageRole = Literal["user", "assistant", "system", "tool"]

# Phase 6 V1.5：共享权限字面量（read / write；owner 隐含 owner 全权）
SharePermission = Literal["read", "write"]


@dataclass
class Session:
    id: str  # UUID
    title: str
    owner: str  # 当前 V0 固定 'default'；V1 接 Phase 10 IAM
    project_name: str  # 当前 V0 固定 'default'；V1 多项目
    status: SessionStatus = "active"
    created_at: int = 0  # 毫秒
    updated_at: int = 0  # 毫秒
    # LangGraph thread_id（同一会话的所有 checkpoint 共享一个 thread_id）
    thread_id: str = ""
    # 元数据 JSON（V1 扩展：tags / 环境名 / 业务域等）
    metadata: dict = field(default_factory=dict)
    # ---- V1.5：分支 ----
    parent_session_id: str | None = None
    branch_from_checkpoint_id: str | None = None
    branch_label: str = ""
    # ---- V1.5：共享权限矩阵 ----
    share_tokens: list[dict] = field(
        default_factory=list
    )  # [{token, permission, created_at, expires_at?}]
    permissions: dict[str, str] = field(default_factory=dict)  # {"alice": "read", ...}
    shared_at: int = 0


@dataclass
class Message:
    id: int = 0  # sqlite 自增
    session_id: str = ""
    role: MessageRole = "user"
    content: str = ""
    created_at: int = 0  # 毫秒
    # 工具调用相关（assistant role 调 tool_call / tool role 返回结果）
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    # 元数据 JSON
    metadata: dict = field(default_factory=dict)


@dataclass
class SessionCheckpoint:
    """LangGraph SqliteSaver 的元数据包装（不存实际状态，只存引用）。

    实际 checkpoint 数据存在 LangGraph 自己的 SQLite 表（thread_id + checkpoint_id）。
    本表只存 user-friendly metadata（label / description / created_at）。
    """

    id: int = 0  # sqlite 自增
    session_id: str = ""
    thread_id: str = ""
    checkpoint_id: str = ""
    label: str = ""
    description: str = ""
    created_at: int = 0  # 毫秒
    metadata: dict = field(default_factory=dict)


# ---- V1.5 新数据类 ---------------------------------------------------------

# 会话级事件哈希链事件类型
SessionEventType = Literal[
    "created",
    "branched",
    "shared",
    "exported",
    "compressed",
    "checkpoint",
    "message_appended",
    "title_changed",
    "status_changed",
]


@dataclass
class SessionEvent:
    """Phase 6 V1.5：会话级事件哈希链节点（SHA-256 链式防篡改）。

    与 audit_expert.signature_chain 同等级：
        hash = SHA256(prev_hash + event_type + payload_json + created_at)
    验证：依序遍历同 session 的事件，若任意一条 hash 与重算不符 → 篡改。
    """

    id: int = 0  # sqlite 自增
    session_id: str = ""
    event_type: SessionEventType = "created"
    payload: dict = field(default_factory=dict)
    prev_hash: str = ""
    hash: str = ""
    actor: str = "system"
    created_at: int = 0


@dataclass
class ShareToken:
    """Phase 6 V1.5：会话分享令牌。

    生成：UUID4 hex + permission + expires_at? → 写入 session.share_tokens_json
    校验：通过 token + 解析 permission + 校验 expires_at → check_access(actor, perm)
    """

    token: str = ""
    permission: SharePermission = "read"
    created_at: int = 0
    expires_at: int | None = None  # 0 / None = 永不过期


@dataclass
class BranchInfo:
    """Phase 6 V1.5：分支派生信息。

    字段：
        parent_session_id: 父会话 ID（None = 主会话）
        branch_from_checkpoint_id: 从父会话的哪个 checkpoint 派生
        branch_label: 分支标签（'bugfix-order-amount' / 'experiment-foo'）
    """

    parent_session_id: str | None = None
    branch_from_checkpoint_id: str | None = None
    branch_label: str = ""


# V1.5 兼容性别名：保留 V0 文档提及的 "context.py 三段式" 接口（虽然实际被
# compression.py 替代），供外部调用者引用。
__all__ = [
    "BranchInfo",
    "Message",
    "MessageRole",
    "Session",
    "SessionCheckpoint",
    "SessionEvent",
    "SessionEventType",
    "SessionStatus",
    "SharePermission",
    "ShareToken",
]
