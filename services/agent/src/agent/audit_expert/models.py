"""Phase 5 V0 · 审核专家模式 —— 数据模型。

V0 范围（金融审计 PoC）：
  - ApprovalTask / ApprovalAction / EvidenceEntry / ComplianceCheck 数据类
  - RiskLevel / ApprovalStatus / ActionType 枚举
  - 签名链辅助（hashlib SHA-256 + prev_hash 链式）

V1 接力：
  - RSA 数字签名（替代 V0 SHA-256 链式 hash）
  - TOTP MFA 真集成
  - OA/IM 集成
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal


# ---- 枚举 -------------------------------------------------------------------

class RiskLevel(str, Enum):
    """风险等级（与 builtin dispatcher TOOL_RISK_LEVEL 对齐）。"""
    READ = "read"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(str, Enum):
    """审批任务状态。"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DELEGATED = "delegated"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class ActionType(str, Enum):
    """审批动作类型。"""
    APPROVE = "approve"
    REJECT = "reject"
    DELEGATE = "delegate"
    INQUIRE = "inquire"   # 询问 / 要求补充信息
    WITHDRAW = "withdraw"


class ComplianceLevel(str, Enum):
    """合规检查级别。"""
    INFO = "info"               # 信息性提示
    WARNING = "warning"         # 警告（建议关注）
    VIOLATION = "violation"     # 违规（必须修复）


class EvidenceType(str, Enum):
    """证据类型。"""
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    HITL_PROMPT = "hitl_prompt"
    HITL_DECISION = "hitl_decision"
    AUDIT_DECISION = "audit_decision"
    FILE_CHANGE = "file_change"
    POLICY_REFERENCE = "policy_reference"


# ---- 数据类 -----------------------------------------------------------------

@dataclass
class ApprovalTask:
    """审批任务（V0 = 来自 HITL 的 pending_tool_call）。

    Attributes:
        task_id: UUID4 hex。
        run_id: LangGraph run_id。
        title: 审批标题（"删除 prod 数据库 users 表"）。
        description: 详细描述。
        risk_level: 风险等级。
        status: 当前状态。
        pending_tool_call: 待执行的工具调用（dict）。
        requested_by: 请求人（OS 用户 / agent name）。
        requested_at: 请求时间。
        decided_by: 决策人。
        decided_at: 决策时间。
        decision_reason: 决策原因（必填）。
        mfa_verified: 是否通过 MFA。
        evidence_count: 关联证据条数。
        compliance_issues: 合规检查问题数。
        meta: 元数据。
        dual_required: 是否需要双人复核（V1 high/critical 默认 True）。
        first_approver: 第一审批人。
        second_approver: 第二审批人。
        first_approver_signed_at: 第一审批时间。
        second_approver_signed_at: 第二审批时间。
    """
    task_id: str
    run_id: str
    title: str
    description: str
    risk_level: RiskLevel
    status: ApprovalStatus = ApprovalStatus.PENDING
    pending_tool_call: dict = field(default_factory=dict)
    requested_by: str = ""
    requested_at: str = ""
    decided_by: str = ""
    decided_at: str = ""
    decision_reason: str = ""
    mfa_verified: bool = False
    evidence_count: int = 0
    compliance_issues: int = 0
    dual_required: bool = False
    first_approver: str = ""
    second_approver: str = ""
    first_approver_signed_at: str = ""
    second_approver_signed_at: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class ApprovalAction:
    """单次审批动作（审计追溯）。

    Attributes:
        action_id: UUID4 hex。
        task_id: 关联 task_id。
        action_type: 动作类型。
        actor: 决策人。
        reason: 决策原因（必填）。
        mfa_verified: MFA 是否通过。
        timestamp: 决策时间。
        prev_hash: 上一个 action 的 hash（链式签名）。
        signature_hash: 当前 action 的 hash（含 prev_hash）。
    """
    action_id: str
    task_id: str
    action_type: ActionType
    actor: str
    reason: str = ""
    mfa_verified: bool = False
    timestamp: str = ""
    prev_hash: str = ""
    signature_hash: str = ""


@dataclass
class EvidenceEntry:
    """证据链条目。

    Attributes:
        evidence_id: UUID4 hex。
        task_id: 关联 task_id。
        evidence_type: 证据类型。
        title: 标题。
        content: 内容（dict）。
        source: 来源（tool_name / actor / system）。
        timestamp: 时间。
        hash: 内容 SHA-256 hash（防篡改）。
    """
    evidence_id: str
    task_id: str
    evidence_type: EvidenceType
    title: str
    content: dict = field(default_factory=dict)
    source: str = ""
    timestamp: str = ""
    hash: str = ""


@dataclass
class ComplianceCheck:
    """合规检查结果。

    Attributes:
        check_id: UUID4 hex。
        task_id: 关联 task_id。
        rule_name: 规则名（如 "PIRED_DESTRUCTIVE_OP" / "OFF_HOURS_DEPLOY"）。
        level: 严重级别。
        message: 检查消息。
        passed: 是否通过。
    """
    check_id: str
    task_id: str
    rule_name: str
    level: ComplianceLevel
    message: str = ""
    passed: bool = True
    timestamp: str = ""


# ---- 签名链（V0 SHA-256，V1 接力 RSA）-----------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_signature(action: ApprovalAction, prev_hash: str = "") -> str:
    """计算签名链 hash（V0 SHA-256）。

    算法：
        payload = action_id|task_id|action_type|actor|reason|mfa_verified|timestamp|prev_hash
        hash = sha256(payload).hexdigest()

    Args:
        action: ApprovalAction（timestamp 可为空，自动填充）。
        prev_hash: 上一个 action 的 signature_hash（链式）。

    Returns:
        signature_hash: 64 hex chars。
    """
    if not action.timestamp:
        action.timestamp = _now_iso()
    if not action.prev_hash and prev_hash:
        action.prev_hash = prev_hash
    payload = "|".join([
        action.action_id,
        action.task_id,
        action.action_type.value,
        action.actor,
        action.reason or "",
        "1" if action.mfa_verified else "0",
        action.timestamp,
        action.prev_hash or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_signature_chain(actions: list[ApprovalAction]) -> bool:
    """校验签名链完整性。

    遍历 actions，每一项重新计算 signature_hash 并比对 prev_hash 与上一项的 signature_hash。

    Returns:
        True if 完整；False if 任意一项被篡改或断链。
    """
    prev = ""
    for action in actions:
        expected = compute_signature(action, prev)
        if action.signature_hash != expected:
            return False
        if action.prev_hash != prev:
            return False
        prev = action.signature_hash
    return True


def generate_id() -> str:
    """生成 UUID4 hex（32 chars）。"""
    return uuid.uuid4().hex


# ---- 工具 --------------------------------------------------------------------

def check_decision_required_fields(action: ApprovalAction) -> list[str]:
    """校验决策动作的必填字段。返缺失字段列表。

    必填：
        - approve / reject: reason + mfa_verified
        - delegate: reason + target_user（通过 meta 传递）
    """
    missing: list[str] = []
    if action.action_type in (ActionType.APPROVE, ActionType.REJECT):
        if not action.reason:
            missing.append("reason")
        if not action.mfa_verified:
            missing.append("mfa_verified")
    if action.action_type == ActionType.DELEGATE:
        if not action.reason:
            missing.append("reason")
    return missing