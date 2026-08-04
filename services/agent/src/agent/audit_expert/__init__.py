"""Phase 5 V1 · 审核专家模式 —— V1 公开 API。

V1 增量（在 V0 8 端点 + SHA-256 签名链基础上）：
  - RSA-2048 数字签名（sign_payload + verify_payload_signature + get_verification_public_key_pem）
  - TOTP MFA（RFC 6238）—— verify_totp + get_current_totp_for_user + get_or_create_user_secret
  - 双人复核（dual_required + first_approver + second_approver 3 字段）
  - FastAPI 12 端点（V0 8 + dual-first + dual-second + mfa/{user} + public-key）
  - 公钥下载端点（GET /audit/public-key）
  - 签名链同时存 SHA-256 chain hash + RSA-PSS signature 双签名（兼容 V0 + V1）

V1.5 接力（生产化）：
  - 删除 demo 端点 GET /audit/mfa/{username}
  - TOTP secret 走 QR 码首次配对（不存默认密钥）
  - 双人复核 RPC 通知（IM 推送）
"""
from __future__ import annotations

from agent.audit_expert.api import router as audit_api_router
from agent.audit_expert.compliance import run_compliance_checks
from agent.audit_expert.events import (
    EVT_AUDIT_COMPLIANCE_DONE,
    EVT_AUDIT_EVIDENCE_ADDED,
    EVT_AUDIT_TASK_DECIDED,
    EVT_AUDIT_TASK_PENDING,
)
from agent.audit_expert.mfa import (
    get_current_totp_for_user,
    get_or_create_user_secret,
    verify_totp,
)
from agent.audit_expert.models import (
    ActionType,
    ApprovalAction,
    ApprovalStatus,
    ApprovalTask,
    ComplianceCheck,
    ComplianceLevel,
    EvidenceEntry,
    EvidenceType,
    RiskLevel,
    check_decision_required_fields,
    compute_signature,
    generate_id,
    verify_signature_chain,
)
from agent.audit_expert.rsa_sign import (
    get_verification_public_key_pem,
    sign_payload,
    verify_payload_signature,
)
from agent.audit_expert.store import (
    AuditExpertStorage,
    get_default_storage,
    reset_default_storage,
)


__all__ = [
    # 数据类
    "ApprovalTask", "ApprovalAction", "EvidenceEntry", "ComplianceCheck",
    # 枚举
    "RiskLevel", "ApprovalStatus", "ActionType", "ComplianceLevel", "EvidenceType",
    # 工具
    "compute_signature", "verify_signature_chain", "generate_id",
    "check_decision_required_fields",
    # V1 RSA
    "sign_payload", "verify_payload_signature", "get_verification_public_key_pem",
    # V1 TOTP MFA
    "verify_totp", "get_current_totp_for_user", "get_or_create_user_secret",
    # 合规
    "run_compliance_checks",
    # 存储
    "AuditExpertStorage", "get_default_storage", "reset_default_storage",
    # 事件常量
    "EVT_AUDIT_TASK_PENDING", "EVT_AUDIT_TASK_DECIDED",
    "EVT_AUDIT_EVIDENCE_ADDED", "EVT_AUDIT_COMPLIANCE_DONE",
    # API router
    "audit_api_router",
]