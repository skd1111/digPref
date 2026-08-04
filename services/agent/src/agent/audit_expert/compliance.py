"""Phase 5 V0 · 合规检查规则 —— PoC 5 条规则。

V0 PoC 规则：
  1. DESTRUCTIVE_OP —— 危险操作（DELETE/DROP/TRUNCATE/REVOKE） → violation
  2. PROD_ENV_RISK —— 目标含 prod / production → warning
  3. OFF_HOURS —— 非工作日 / 凌晨 0-6 → info
  4. MISSING_EVIDENCE —— 证据数 < 2 → warning
  5. HIGH_RISK_NO_MFA —— high/critical 风险但无 MFA 配置 → violation

V1 接力：
  - 配置文件驱动的规则引擎（YAML / 数据库）
  - 跨会话规则（合规黑名单）
  - 自动阻断（vs 仅提示）
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from agent.audit_expert.models import (
    ComplianceCheck,
    ComplianceLevel,
    RiskLevel,
    generate_id,
)


# 危险操作正则（DESTRUCTIVE_OP）
_DESTRUCTIVE_KEYWORDS = re.compile(
    r"\b(DELETE|DROP|TRUNCATE|REVOKE|GRANT|ALTER\s+TABLE|DESTROY|REMOVE\s+ALL)\b",
    re.IGNORECASE,
)

# 生产环境识别
_PROD_ENV_PATTERN = re.compile(
    r"(prod|production|prodcn|prod-us|prod-eu|生产|线上)",
    re.IGNORECASE,
)

# 工作时间：8:00-20:00（UTC+8，简化处理）
_WORK_HOURS_START = 8
_WORK_HOURS_END = 20


def _now_utc_hour() -> int:
    return datetime.now(timezone.utc).astimezone().hour


def run_compliance_checks(
    *,
    task_id: str,
    risk_level: RiskLevel,
    pending_tool_call: dict,
    evidence_count: int,
    mfa_configured: bool = True,
) -> list[ComplianceCheck]:
    """运行合规检查（V0 5 条规则），返 check 列表。

    Args:
        task_id: 关联任务。
        risk_level: 任务风险等级。
        pending_tool_call: 待执行的工具调用。
        evidence_count: 已收集证据条数。
        mfa_configured: 当前用户是否配置 MFA（V0 假设 True）。

    Returns:
        list[ComplianceCheck]
    """
    checks: list[ComplianceCheck] = []
    tool_name = str(pending_tool_call.get("name") or pending_tool_call.get("server") or "")
    tool_args_str = str(pending_tool_call.get("args") or "")

    # 1. DESTRUCTIVE_OP
    if _DESTRUCTIVE_KEYWORDS.search(tool_name) or _DESTRUCTIVE_KEYWORDS.search(tool_args_str):
        checks.append(ComplianceCheck(
            check_id=generate_id(),
            task_id=task_id,
            rule_name="DESTRUCTIVE_OP",
            level=ComplianceLevel.VIOLATION,
            message=f"危险操作：'{tool_name}' 含 DELETE/DROP/TRUNCATE 等关键字；必须 reject",
            passed=False,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))
    else:
        checks.append(ComplianceCheck(
            check_id=generate_id(),
            task_id=task_id,
            rule_name="DESTRUCTIVE_OP",
            level=ComplianceLevel.INFO,
            message="无危险操作关键字",
            passed=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

    # 2. PROD_ENV_RISK
    if _PROD_ENV_PATTERN.search(tool_name) or _PROD_ENV_PATTERN.search(tool_args_str):
        checks.append(ComplianceCheck(
            check_id=generate_id(),
            task_id=task_id,
            rule_name="PROD_ENV_RISK",
            level=ComplianceLevel.WARNING,
            message="目标可能涉及生产环境；建议双人复核 + MFA 强制",
            passed=True,  # 警告通过，但提示用户
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

    # 3. OFF_HOURS
    hour = _now_utc_hour()
    if hour < _WORK_HOURS_START or hour >= _WORK_HOURS_END:
        checks.append(ComplianceCheck(
            check_id=generate_id(),
            task_id=task_id,
            rule_name="OFF_HOURS",
            level=ComplianceLevel.INFO,
            message=f"非工作时间操作（当前 {hour}:xx UTC+8）；建议延后或加批注",
            passed=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

    # 4. MISSING_EVIDENCE
    if evidence_count < 2:
        checks.append(ComplianceCheck(
            check_id=generate_id(),
            task_id=task_id,
            rule_name="MISSING_EVIDENCE",
            level=ComplianceLevel.WARNING,
            message=f"证据数不足（{evidence_count} < 2）；建议补充操作目的 / 影响范围",
            passed=False,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

    # 5. HIGH_RISK_NO_MFA
    if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and not mfa_configured:
        checks.append(ComplianceCheck(
            check_id=generate_id(),
            task_id=task_id,
            rule_name="HIGH_RISK_NO_MFA",
            level=ComplianceLevel.VIOLATION,
            message=f"{risk_level.value} 风险任务但用户未配置 MFA；必须 reject",
            passed=False,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

    return checks