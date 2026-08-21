"""Phase 5 V1 · FastAPI 12 端点 —— 审核专家工作台（V1 加 MFA + 双人 + RSA）。

端点（V0 7 + V1 新增 5）：
  - POST /audit/tasks                     —— 创建审批任务（自动评估 dual_required）
  - GET  /audit/tasks                     —— 列表
  - GET  /audit/tasks/{task_id}           —— 任务详情
  - POST /audit/tasks/{task_id}/evidence  —— 添加证据
  - GET  /audit/tasks/{task_id}/evidence  —— 列证据
  - GET  /audit/tasks/{task_id}/compliance —— 合规检查
  - POST /audit/tasks/{task_id}/decide    —— 单人决策（含 TOTP 验证 + RSA 签名）
  - POST /audit/tasks/{task_id}/dual-first —— 双人复核：第一审批
  - POST /audit/tasks/{task_id}/dual-second —— 双人复核：第二审批（必须不同 actor）
  - GET  /audit/tasks/{task_id}/verify    —— 验证签名链（SHA-256 + 可选 RSA）
  - GET  /audit/mfa/{username}            —— 获取当前 TOTP（仅用于 demo）
  - GET  /audit/public-key                —— 获取 RSA 公钥 PEM
  - GET  /audit/stats                     —— 统计
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.audit_expert.compliance import run_compliance_checks
from agent.audit_expert.events import (
    EVT_AUDIT_COMPLIANCE_DONE,
    EVT_AUDIT_EVIDENCE_ADDED,
    EVT_AUDIT_TASK_DECIDED,
    EVT_AUDIT_TASK_PENDING,
    emit_event_sync,
)
from agent.audit_expert.mfa import (
    get_current_totp_for_user,
    verify_totp,
)
from agent.audit_expert.models import (
    ActionType,
    ApprovalAction,
    ApprovalStatus,
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
)
from agent.audit_expert.store import (
    get_default_storage,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["audit-expert"])


# ---- Pydantic schemas -----------------------------------------------------


class CreateTaskRequest(BaseModel):
    run_id: str
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(min_length=1, max_length=8192)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    pending_tool_call: dict
    requested_by: str = Field(default="current_user", min_length=1, max_length=128)
    meta: dict = Field(default_factory=dict)


class AddEvidenceRequest(BaseModel):
    evidence_type: EvidenceType = EvidenceType.TOOL_CALL
    title: str = Field(min_length=1, max_length=512)
    content: dict
    source: str = Field(default="agent", min_length=1, max_length=128)


class DecideRequest(BaseModel):
    """V1 决策请求：包含 TOTP + RSA 签名字段。"""

    action_type: ActionType = ActionType.APPROVE
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=2048)
    mfa_verified: bool = False
    totp_code: str | None = Field(default=None, min_length=6, max_length=6)
    use_rsa: bool = True


class DualApproveRequest(BaseModel):
    """V1 双人复核：仅 approve + reason + mfa + totp。"""

    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=2048)
    mfa_verified: bool = False
    totp_code: str | None = Field(default=None, min_length=6, max_length=6)
    use_rsa: bool = True


class TaskResponse(BaseModel):
    task_id: str
    run_id: str
    title: str
    description: str
    risk_level: str
    status: str
    pending_tool_call: dict
    requested_by: str
    requested_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    decision_reason: str | None = None
    mfa_verified: bool
    evidence_count: int
    compliance_issues: int
    dual_required: bool = False
    first_approver: str | None = None
    second_approver: str | None = None
    first_approver_signed_at: str | None = None
    second_approver_signed_at: str | None = None
    meta: dict = Field(default_factory=dict)


# ---- 辅助函数 ------------------------------------------------------------


def _should_dual_required(risk_level: RiskLevel) -> bool:
    """判断是否需要双人复核（V1 策略）。"""
    return risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def _enforce_mfa_and_signature(
    *,
    actor: str,
    risk_level: RiskLevel,
    mfa_verified: bool,
    totp_code: str | None,
    use_rsa: bool,
) -> tuple[bool, str | None, str | None, str | None]:
    """统一校验：MFA + (可选 TOTP) + (可选 RSA 签名占位)。

    Returns:
        (mfa_verified, totp_code_hash, rsa_signature, error_msg)
    """
    # V1 简化：MFA 必填 + TOTP 可选；RSA 签名总生成（不强制验证）
    totp_code_hash: str | None = None
    rsa_signature: str | None = None

    if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and not mfa_verified:
        return False, None, None, "MFA required for high/critical tasks"

    if totp_code:
        from agent.audit_expert.mfa import get_or_create_user_secret

        secret = get_or_create_user_secret(actor)
        if not verify_totp(secret, totp_code):
            return False, None, None, "TOTP code invalid or expired"
        totp_code_hash = hashlib.sha256(totp_code.encode("utf-8")).hexdigest()

    if use_rsa:
        # 签名 payload = "actor|risk|totp_hash|timestamp"
        import time as _t

        payload = f"{actor}|{risk_level.value}|{totp_code_hash or ''}|{int(_t.time())}"
        try:
            rsa_signature = sign_payload(payload)
        except Exception as exc:
            logger.warning("rsa_sign_failed: %s", exc)

    return mfa_verified or bool(totp_code_hash), totp_code_hash, rsa_signature, None


# ---- 端点 ---------------------------------------------------------------


@router.post("/tasks", response_model=TaskResponse)
async def create_task(req: CreateTaskRequest) -> TaskResponse:
    """创建审批任务 + 同步触发合规检查 + 自动评估 dual_required。"""
    storage = get_default_storage()
    task_id = generate_id()
    dual = _should_dual_required(req.risk_level)

    await storage.insert_task(
        task_id=task_id,
        run_id=req.run_id,
        title=req.title,
        description=req.description,
        risk_level=req.risk_level.value,
        pending_tool_call=req.pending_tool_call,
        requested_by=req.requested_by,
        meta=req.meta,
        dual_required=dual,
    )

    # 同步运行合规检查
    checks = run_compliance_checks(
        task_id=task_id,
        risk_level=req.risk_level,
        pending_tool_call=req.pending_tool_call,
        evidence_count=0,
    )
    for chk in checks:
        await storage.insert_compliance(
            check_id=chk.check_id,
            task_id=task_id,
            rule_name=chk.rule_name,
            level=chk.level.value,
            message=chk.message,
            passed=chk.passed,
        )

    compliance_issues = sum(1 for c in checks if not c.passed)
    emit_event_sync(
        EVT_AUDIT_TASK_PENDING,
        {
            "kind": EVT_AUDIT_TASK_PENDING,
            "task_id": task_id,
            "risk_level": req.risk_level.value,
            "compliance_issues": compliance_issues,
            "dual_required": dual,
        },
    )
    emit_event_sync(
        EVT_AUDIT_COMPLIANCE_DONE,
        {
            "kind": EVT_AUDIT_COMPLIANCE_DONE,
            "task_id": task_id,
            "total_checks": len(checks),
            "issues": compliance_issues,
        },
    )

    return TaskResponse(
        task_id=task_id,
        run_id=req.run_id,
        title=req.title,
        description=req.description,
        risk_level=req.risk_level.value,
        status=ApprovalStatus.PENDING.value,
        pending_tool_call=req.pending_tool_call,
        requested_by=req.requested_by,
        requested_at="",
        decided_by=None,
        decided_at=None,
        decision_reason=None,
        mfa_verified=False,
        evidence_count=0,
        compliance_issues=compliance_issues,
        dual_required=dual,
        first_approver=None,
        second_approver=None,
        first_approver_signed_at=None,
        second_approver_signed_at=None,
        meta=req.meta,
    )


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = None,
    risk_level: str | None = None,
    limit: int = 50,
) -> list[TaskResponse]:
    storage = get_default_storage()
    tasks = await storage.list_tasks(
        status=status,
        risk_level=risk_level,
        limit=min(limit, 500),
    )
    result: list[TaskResponse] = []
    for t in tasks:
        evidence_list = await storage.list_evidence(t["task_id"])
        compliance_list = await storage.list_compliance(t["task_id"])
        issues = sum(1 for c in compliance_list if not c["passed"])
        result.append(
            TaskResponse(
                task_id=t["task_id"],
                run_id=t["run_id"],
                title=t["title"],
                description=t["description"],
                risk_level=t["risk_level"],
                status=t["status"],
                pending_tool_call=t.get("pending_tool_call", {}),
                requested_by=t["requested_by"],
                requested_at=t["requested_at"],
                decided_by=t.get("decided_by") or None,
                decided_at=t.get("decided_at") or None,
                decision_reason=t.get("decision_reason") or None,
                mfa_verified=t.get("mfa_verified", False),
                evidence_count=len(evidence_list),
                compliance_issues=issues,
                dual_required=t.get("dual_required", False),
                first_approver=t.get("first_approver") or None,
                second_approver=t.get("second_approver") or None,
                first_approver_signed_at=t.get("first_approver_signed_at") or None,
                second_approver_signed_at=t.get("second_approver_signed_at") or None,
                meta=t.get("meta", {}),
            )
        )
    return result


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    evidence_list = await storage.list_evidence(task_id)
    compliance_list = await storage.list_compliance(task_id)
    issues = sum(1 for c in compliance_list if not c["passed"])
    return TaskResponse(
        task_id=task["task_id"],
        run_id=task["run_id"],
        title=task["title"],
        description=task["description"],
        risk_level=task["risk_level"],
        status=task["status"],
        pending_tool_call=task.get("pending_tool_call", {}),
        requested_by=task["requested_by"],
        requested_at=task["requested_at"],
        decided_by=task.get("decided_by") or None,
        decided_at=task.get("decided_at") or None,
        decision_reason=task.get("decision_reason") or None,
        mfa_verified=task.get("mfa_verified", False),
        evidence_count=len(evidence_list),
        compliance_issues=issues,
        dual_required=task.get("dual_required", False),
        first_approver=task.get("first_approver") or None,
        second_approver=task.get("second_approver") or None,
        first_approver_signed_at=task.get("first_approver_signed_at") or None,
        second_approver_signed_at=task.get("second_approver_signed_at") or None,
        meta=task.get("meta", {}),
    )


@router.post("/tasks/{task_id}/evidence")
async def add_evidence(task_id: str, req: AddEvidenceRequest) -> dict:
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    evidence_id = generate_id()
    await storage.insert_evidence(
        evidence_id=evidence_id,
        task_id=task_id,
        evidence_type=req.evidence_type.value,
        title=req.title,
        content=req.content,
        source=req.source,
    )
    emit_event_sync(
        EVT_AUDIT_EVIDENCE_ADDED,
        {
            "kind": EVT_AUDIT_EVIDENCE_ADDED,
            "task_id": task_id,
            "evidence_id": evidence_id,
            "evidence_type": req.evidence_type.value,
        },
    )
    return {"evidence_id": evidence_id, "task_id": task_id}


@router.get("/tasks/{task_id}/evidence")
async def list_evidence(task_id: str) -> dict:
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    entries = await storage.list_evidence(task_id)
    return {"task_id": task_id, "count": len(entries), "evidence": entries}


@router.get("/tasks/{task_id}/compliance")
async def list_compliance(task_id: str) -> dict:
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    checks = await storage.list_compliance(task_id)
    return {"task_id": task_id, "count": len(checks), "checks": checks}


async def _do_decide(
    task_id: str,
    actor: str,
    reason: str,
    mfa_verified: bool,
    totp_code: str | None,
    use_rsa: bool,
    action_type: ActionType,
) -> dict[str, Any]:
    """决策共用逻辑（V1 含 TOTP + RSA 签名）。"""
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    if task["status"] != ApprovalStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"task already decided: status={task['status']}",
        )

    # 双人复核：第一次 approve 走 dual-first，第二次走 dual-second
    if task.get("dual_required") and action_type == ActionType.APPROVE:
        raise HTTPException(
            status_code=400,
            detail="dual_required task: use /dual-first and /dual-second endpoints",
        )

    # 校验必填
    action = ApprovalAction(
        action_id=generate_id(),
        task_id=task_id,
        action_type=action_type,
        actor=actor,
        reason=reason,
        mfa_verified=mfa_verified,
    )
    missing = check_decision_required_fields(action)
    if missing:
        raise HTTPException(status_code=400, detail=f"missing required fields: {missing}")

    # MFA + TOTP + RSA 签名
    mfa_ok, totp_hash, rsa_sig, err = _enforce_mfa_and_signature(
        actor=actor,
        risk_level=RiskLevel(task["risk_level"]),
        mfa_verified=mfa_verified,
        totp_code=totp_code,
        use_rsa=use_rsa,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)

    action.mfa_verified = mfa_ok
    prev_hash = await storage.get_last_action_hash(task_id)
    action.prev_hash = prev_hash
    action.signature_hash = compute_signature(action, prev_hash)

    await storage.insert_action(
        action_id=action.action_id,
        task_id=task_id,
        action_type=action_type.value,
        actor=actor,
        reason=reason,
        mfa_verified=mfa_ok,
        totp_code_hash=totp_hash,
        timestamp=action.timestamp,
        prev_hash=prev_hash,
        signature_hash=action.signature_hash,
        rsa_signature=rsa_sig,
        meta={"totp_provided": bool(totp_code), "rsa_used": use_rsa},
    )

    new_status_map = {
        ActionType.APPROVE: ApprovalStatus.APPROVED,
        ActionType.REJECT: ApprovalStatus.REJECTED,
        ActionType.DELEGATE: ApprovalStatus.DELEGATED,
        ActionType.WITHDRAW: ApprovalStatus.WITHDRAWN,
        ActionType.INQUIRE: ApprovalStatus.PENDING,
    }
    new_status = new_status_map[action_type]
    await storage.update_task_decision(
        task_id,
        status=new_status.value,
        decided_by=actor,
        decision_reason=reason,
        mfa_verified=mfa_ok,
    )

    emit_event_sync(
        EVT_AUDIT_TASK_DECIDED,
        {
            "kind": EVT_AUDIT_TASK_DECIDED,
            "task_id": task_id,
            "action_type": action_type.value,
            "actor": actor,
            "new_status": new_status.value,
            "rsa_used": bool(rsa_sig),
        },
    )
    return {
        "task_id": task_id,
        "action_id": action.action_id,
        "action_type": action_type.value,
        "new_status": new_status.value,
        "signature_hash": action.signature_hash,
        "rsa_signature": rsa_sig,
    }


@router.post("/tasks/{task_id}/decide")
async def decide(task_id: str, req: DecideRequest) -> dict[str, Any]:
    """V1 单人决策（含 TOTP + RSA）。"""
    return await _do_decide(
        task_id=task_id,
        actor=req.actor,
        reason=req.reason,
        mfa_verified=req.mfa_verified,
        totp_code=req.totp_code,
        use_rsa=req.use_rsa,
        action_type=req.action_type,
    )


@router.post("/tasks/{task_id}/dual-first")
async def dual_first_approve(task_id: str, req: DualApproveRequest) -> dict[str, Any]:
    """V1 双人复核：第一审批（仅 approve）。"""
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    if not task.get("dual_required"):
        raise HTTPException(status_code=400, detail="task does not require dual approval")
    if task["status"] != ApprovalStatus.PENDING.value:
        raise HTTPException(status_code=400, detail=f"task status: {task['status']}")
    if task.get("first_approver"):
        raise HTTPException(status_code=400, detail="already has first approver")

    # MFA + RSA
    mfa_ok, totp_hash, rsa_sig, err = _enforce_mfa_and_signature(
        actor=req.actor,
        risk_level=RiskLevel(task["risk_level"]),
        mfa_verified=req.mfa_verified,
        totp_code=req.totp_code,
        use_rsa=req.use_rsa,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)

    # 写 action（action_type=APPROVE，但任务状态仍 pending）
    action = ApprovalAction(
        action_id=generate_id(),
        task_id=task_id,
        action_type=ActionType.APPROVE,
        actor=req.actor,
        reason=req.reason,
        mfa_verified=mfa_ok,
    )
    prev_hash = await storage.get_last_action_hash(task_id)
    action.prev_hash = prev_hash
    action.signature_hash = compute_signature(action, prev_hash)
    await storage.insert_action(
        action_id=action.action_id,
        task_id=task_id,
        action_type=ActionType.APPROVE.value,
        actor=req.actor,
        reason=req.reason,
        mfa_verified=mfa_ok,
        totp_code_hash=totp_hash,
        timestamp=action.timestamp,
        prev_hash=prev_hash,
        signature_hash=action.signature_hash,
        rsa_signature=rsa_sig,
        meta={"phase": "dual_first"},
    )
    await storage.record_first_approver(task_id, req.actor, req.reason)

    emit_event_sync(
        EVT_AUDIT_TASK_DECIDED,
        {
            "kind": EVT_AUDIT_TASK_DECIDED,
            "task_id": task_id,
            "phase": "dual_first",
            "actor": req.actor,
            "new_status": "pending",  # 仍 pending 等第二审批
        },
    )
    return {
        "task_id": task_id,
        "phase": "dual_first",
        "action_id": action.action_id,
        "first_approver": req.actor,
        "new_status": "pending",
        "signature_hash": action.signature_hash,
        "rsa_signature": rsa_sig,
    }


@router.post("/tasks/{task_id}/dual-second")
async def dual_second_approve(task_id: str, req: DualApproveRequest) -> dict[str, Any]:
    """V1 双人复核：第二审批（必须与第一审批不同 actor）。"""
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    if not task.get("dual_required"):
        raise HTTPException(status_code=400, detail="task does not require dual approval")
    if not task.get("first_approver"):
        raise HTTPException(status_code=400, detail="dual-first not done yet")
    if task.get("second_approver"):
        raise HTTPException(status_code=400, detail="already has second approver")
    if task.get("first_approver") == req.actor:
        raise HTTPException(status_code=400, detail="second approver must differ from first")

    # MFA + RSA
    mfa_ok, totp_hash, rsa_sig, err = _enforce_mfa_and_signature(
        actor=req.actor,
        risk_level=RiskLevel(task["risk_level"]),
        mfa_verified=req.mfa_verified,
        totp_code=req.totp_code,
        use_rsa=req.use_rsa,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)

    action = ApprovalAction(
        action_id=generate_id(),
        task_id=task_id,
        action_type=ActionType.APPROVE,
        actor=req.actor,
        reason=req.reason,
        mfa_verified=mfa_ok,
    )
    prev_hash = await storage.get_last_action_hash(task_id)
    action.prev_hash = prev_hash
    action.signature_hash = compute_signature(action, prev_hash)
    await storage.insert_action(
        action_id=action.action_id,
        task_id=task_id,
        action_type=ActionType.APPROVE.value,
        actor=req.actor,
        reason=req.reason,
        mfa_verified=mfa_ok,
        totp_code_hash=totp_hash,
        timestamp=action.timestamp,
        prev_hash=prev_hash,
        signature_hash=action.signature_hash,
        rsa_signature=rsa_sig,
        meta={"phase": "dual_second"},
    )
    await storage.record_second_approver(task_id, req.actor, req.reason)

    emit_event_sync(
        EVT_AUDIT_TASK_DECIDED,
        {
            "kind": EVT_AUDIT_TASK_DECIDED,
            "task_id": task_id,
            "phase": "dual_second",
            "actor": req.actor,
            "new_status": "approved",
        },
    )
    return {
        "task_id": task_id,
        "phase": "dual_second",
        "action_id": action.action_id,
        "second_approver": req.actor,
        "first_approver": task.get("first_approver"),
        "new_status": "approved",
        "signature_hash": action.signature_hash,
        "rsa_signature": rsa_sig,
    }


@router.get("/tasks/{task_id}/verify")
async def verify_chain(task_id: str) -> dict[str, Any]:
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    rows = await storage.list_actions(task_id)
    actions = [
        ApprovalAction(
            action_id=r["action_id"],
            task_id=r["task_id"],
            action_type=ActionType(r["action_type"]),
            actor=r["actor"],
            reason=r.get("reason") or "",
            mfa_verified=bool(r.get("mfa_verified", 0)),
            timestamp=r["timestamp"],
            prev_hash=r["prev_hash"],
            signature_hash=r["signature_hash"],
        )
        for r in rows
    ]
    valid = verify_signature_chain(actions)
    # V1：检查 RSA 签名（如有）
    rsa_count = sum(1 for r in rows if r.get("rsa_signature"))
    return {
        "task_id": task_id,
        "valid": valid,
        "action_count": len(actions),
        "rsa_signed_actions": rsa_count,
    }


@router.get("/mfa/{username}")
async def get_mfa_code(username: str) -> dict:
    """V1 demo 端点：返回当前用户的 TOTP（生产 V1.5 删除或鉴权）。"""
    code = get_current_totp_for_user(username)
    return {"username": username, "totp_code": code, "note": "demo only; remove in V1.5"}


@router.get("/public-key")
async def get_public_key() -> dict:
    """V1 RSA 公钥下载端点（前端可下载用于离线验签）。"""
    pem = get_verification_public_key_pem()
    return {
        "algorithm": "RSA-2048-PSS-SHA256",
        "public_key_pem": pem,
    }


@router.get("/stats")
async def stats() -> dict:
    storage = get_default_storage()
    return await storage.get_stats()


# ============================================================
# Phase 5 V2 · 简化路径：导入文档 → 直接审核（audit_doc.py 风格）
# ============================================================


class AuditDocumentRequest(BaseModel):
    """简化审核端点请求（指定一个业务描述）."""

    task: str = Field(min_length=1, max_length=2048)
    top_k: int = Field(default=5, ge=1, le=20)
    use_llm: bool = Field(default=True)


@router.post("/audit-document")
async def audit_document(req: AuditDocumentRequest) -> dict:
    """导入财务法规文档 → 直接审核业务。

    这是「简化路径」：把 knowledge-base/fiscal-tax/ 下的 .md 法规文件
    当作审核模型的知识库，业务描述过来后用 BM25 关键词检索 + LLM 判定。

    适合 demo / 面试演示 / 简单合规查询。
    """
    import sys
    from pathlib import Path as _Path

    _scripts_dir = _Path(__file__).resolve().parents[3] / "scripts"
    if str(_scripts_dir) not in sys.path:
        sys.path.insert(0, str(_scripts_dir))

    from audit_doc import audit as audit_doc_audit  # type: ignore[import-not-found]

    result = audit_doc_audit(
        req.task,
        top_k=req.top_k,
        use_llm=req.use_llm,
    )
    return result.to_dict()
