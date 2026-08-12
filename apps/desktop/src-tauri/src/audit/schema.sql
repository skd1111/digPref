-- Mirror of services/agent/src/agent/audit/schema.sql.
-- Two processes (Rust + Python) share this table — both append-only, no migrations.
-- V1.5 (2026-07-31): 加 5 列用于子 Agent 决策树回放
CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    payload         TEXT NOT NULL,            -- JSON-encoded
    ts              TEXT NOT NULL,            -- RFC 3339
    operator        TEXT,                     -- OS user (filled by Rust side)
    run_id          TEXT,                     -- LangGraph run id (filled by Python side)
    correlation_id  TEXT,
    actor_type      TEXT,
    event_type      TEXT,
    task_id         TEXT,
    parent_task_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_action      ON audit(action);
CREATE INDEX IF NOT EXISTS idx_audit_ts          ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_run         ON audit(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit(correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor_event ON audit(actor_type, event_type);
CREATE INDEX IF NOT EXISTS idx_audit_task        ON audit(task_id);

-- Phase 1B V1 (2026-07-30): 原生工具层 tool_calls 表（Python + Rust 双 schema 同步）
-- Python 侧由 ToolDispatcher 写入；Rust 侧在 V1.5 接力（届时 Tauri command 也可写）
-- 双 schema 严格镜像 —— Python INSERT 列序 / Rust INSERT 列序须一致（CLAUDE.md §6）
CREATE TABLE IF NOT EXISTS tool_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id         TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    needs_hitl      INTEGER NOT NULL DEFAULT 0,
    ok              INTEGER NOT NULL,
    error           TEXT,
    args_json       TEXT NOT NULL,
    run_id          TEXT,
    operator        TEXT,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    content_size    INTEGER NOT NULL DEFAULT 0,
    approval_id     TEXT,
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run    ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool   ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ts     ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_tool_calls_call   ON tool_calls(call_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_risk   ON tool_calls(risk_level, ts);