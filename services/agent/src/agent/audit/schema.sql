-- Mirror of apps/desktop/src-tauri/src/audit/schema.sql
-- Both processes share the same SQLite file (read-only for the other).
-- V1.5 (2026-07-31): 加 5 列用于子 Agent 决策树回放 —— correlation_id / actor_type / event_type / task_id / parent_task_id
CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    payload         TEXT NOT NULL,
    ts              TEXT NOT NULL,
    operator        TEXT,
    run_id          TEXT,
    -- Phase 12 V1.5：子 Agent 决策树串联（spawn / progress / done / hitl / judge）
    correlation_id  TEXT,
    actor_type      TEXT,        -- 'user' / 'main_agent' / 'sub_agent' / 'system'
    event_type      TEXT,        -- 结构化事件名（与 action 平行）
    task_id         TEXT,        -- 子 Agent sub_agent_id（主 Agent 操作时为 None）
    parent_task_id  TEXT         -- 父 sub_agent_id（递归回放）
);
CREATE INDEX IF NOT EXISTS idx_audit_action      ON audit(action);
CREATE INDEX IF NOT EXISTS idx_audit_ts          ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_run         ON audit(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit(correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor_event ON audit(actor_type, event_type);
CREATE INDEX IF NOT EXISTS idx_audit_task        ON audit(task_id);

-- Phase 1B V1 (2026-07-30): 原生工具层 tool_calls 表（Python + Rust 双 schema 同步）
-- 与 audit 表平级，专门记录 builtin_* 工具调用。
-- 与 audit.action='builtin_tool' 的区别：tool_calls 结构化列便于查询/聚合；
-- audit 表只保留精简 payload（向后兼容 + 已有索引）。
CREATE TABLE IF NOT EXISTS tool_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 调用标识（UUID4 hex，由 dispatcher 生成）
    call_id         TEXT NOT NULL,
    -- 工具名（不含 builtin_ 前缀）
    tool_name       TEXT NOT NULL,
    -- 风险等级（read / low / medium / high / critical）
    risk_level      TEXT NOT NULL,
    -- 是否触发 HITL
    needs_hitl      INTEGER NOT NULL DEFAULT 0,
    -- 执行结果（true / false）
    ok              INTEGER NOT NULL,
    -- 错误码（None 表示成功）
    error           TEXT,
    -- scrub 后的参数（只保留 keys 列表 + 文件 basename + size）
    args_json       TEXT NOT NULL,
    -- 调用方上下文
    run_id          TEXT,
    operator        TEXT,
    -- 执行耗时（毫秒）
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    -- 结果大小（字节；content=None 时为 0）
    content_size    INTEGER NOT NULL DEFAULT 0,
    -- HITL approval_id（HITL 触发时记录；None 表示未触发）
    approval_id     TEXT,
    -- ISO 8601 UTC 时间戳
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run    ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool   ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ts     ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_tool_calls_call   ON tool_calls(call_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_risk   ON tool_calls(risk_level, ts);