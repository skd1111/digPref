-- Phase 5 V0 · 审核专家模式 4 表（audit_expert.db）
-- 与 Phase 1 audit 表 + Phase 1B tool_calls 表物理隔离
CREATE TABLE IF NOT EXISTS approval_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL UNIQUE,
    run_id          TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    risk_level      TEXT NOT NULL,                          -- read / low / medium / high / critical
    status          TEXT NOT NULL DEFAULT 'pending',       -- pending / approved / rejected / delegated / withdrawn / expired
    pending_tool_call_json TEXT NOT NULL,
    requested_by    TEXT NOT NULL,
    requested_at    TEXT NOT NULL,
    decided_by      TEXT,
    decided_at      TEXT,
    decision_reason TEXT,
    mfa_verified    INTEGER NOT NULL DEFAULT 0,
    -- V1: 双人复核
    dual_required   INTEGER NOT NULL DEFAULT 0,
    first_approver  TEXT,
    second_approver TEXT,
    first_approver_signed_at  TEXT,
    second_approver_signed_at TEXT,
    meta_json       TEXT NOT NULL DEFAULT '{}',
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_appr_tasks_id     ON approval_tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_appr_tasks_status ON approval_tasks(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_appr_tasks_risk   ON approval_tasks(risk_level, status);
CREATE INDEX IF NOT EXISTS idx_appr_tasks_run    ON approval_tasks(run_id);

-- 审批动作流水（含签名链；V1 用 RSA-2048 签名替换 SHA-256）
CREATE TABLE IF NOT EXISTS approval_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id       TEXT NOT NULL UNIQUE,
    task_id         TEXT NOT NULL,
    action_type     TEXT NOT NULL,                          -- approve / reject / delegate / inquire / withdraw
    actor           TEXT NOT NULL,
    reason          TEXT,
    mfa_verified    INTEGER NOT NULL DEFAULT 0,
    -- V1: TOTP 验证码（生产应审计但不持久化原文；V1 简化为审计字段）
    totp_code_hash  TEXT,
    timestamp       TEXT NOT NULL,
    prev_hash       TEXT NOT NULL DEFAULT '',
    signature_hash  TEXT NOT NULL,
    -- V1: RSA 签名 base64
    rsa_signature   TEXT,
    meta_json       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_appr_actions_task ON approval_actions(task_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_appr_actions_hash ON approval_actions(signature_hash);
CREATE INDEX IF NOT EXISTS idx_appr_actions_actor ON approval_actions(actor, timestamp);

-- 证据链（防篡改 SHA-256 hash）
CREATE TABLE IF NOT EXISTS evidence_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id     TEXT NOT NULL UNIQUE,
    task_id         TEXT NOT NULL,
    evidence_type   TEXT NOT NULL,
    title           TEXT NOT NULL,
    content_json    TEXT NOT NULL,
    source          TEXT,
    timestamp       TEXT NOT NULL,
    hash            TEXT NOT NULL                           -- SHA-256 of content_json
);
CREATE INDEX IF NOT EXISTS idx_evidence_task ON evidence_entries(task_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_evidence_hash ON evidence_entries(hash);

-- 合规检查
CREATE TABLE IF NOT EXISTS compliance_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id        TEXT NOT NULL UNIQUE,
    task_id         TEXT NOT NULL,
    rule_name       TEXT NOT NULL,
    level           TEXT NOT NULL,                          -- info / warning / violation
    message         TEXT,
    passed          INTEGER NOT NULL DEFAULT 1,
    timestamp       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_compliance_task  ON compliance_checks(task_id);
CREATE INDEX IF NOT EXISTS idx_compliance_rule  ON compliance_checks(rule_name);
CREATE INDEX IF NOT EXISTS idx_compliance_level ON compliance_checks(level, passed);