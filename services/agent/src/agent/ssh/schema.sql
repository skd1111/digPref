-- Phase 2B V0 · ssh_sessions 表（ssh.db）
-- 记录每次 SSH 会话连接 + 命令执行历史，供审计 + 历史回溯
CREATE TABLE IF NOT EXISTS ssh_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,                    -- UUID4 hex
    host            TEXT NOT NULL,
    port            INTEGER NOT NULL DEFAULT 22,
    username        TEXT NOT NULL,
    auth_method     TEXT NOT NULL,                    -- password / publickey / none
    status          TEXT NOT NULL,                    -- connected / disconnected / error
    pty_mode        TEXT NOT NULL DEFAULT 'echo',
    created_at      TEXT NOT NULL,
    last_used       TEXT,
    disconnected_at TEXT,
    meta_json       TEXT NOT NULL DEFAULT '{}',
    -- 错误信息（如认证失败）
    error           TEXT,
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ssh_sessions_id     ON ssh_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_ssh_sessions_host   ON ssh_sessions(host, username);
CREATE INDEX IF NOT EXISTS idx_ssh_sessions_ts     ON ssh_sessions(ts);
CREATE INDEX IF NOT EXISTS idx_ssh_sessions_status ON ssh_sessions(status, ts);

-- ssh_commands 表：每次命令执行记录（独立审计）
CREATE TABLE IF NOT EXISTS ssh_commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    command         TEXT NOT NULL,                    -- sanitize 后的完整命令
    exit_code       INTEGER,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    ok              INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    -- 截断保存（防止超大输出撑爆 DB；前 4KB stdout + 前 4KB stderr）
    stdout_head     TEXT,
    stderr_head     TEXT,
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ssh_cmds_session ON ssh_commands(session_id);
CREATE INDEX IF NOT EXISTS idx_ssh_cmds_ts       ON ssh_commands(ts);
CREATE INDEX IF NOT EXISTS idx_ssh_cmds_ok       ON ssh_commands(ok, ts);