-- Phase 12 V1.5 —— Orchestrator 权威持久层（orchestrator.db）
--
-- 物理隔离（CLAUDE.md §6）：与 audit / router / knowledge / biznav / codenav /
-- sessions / log_analysis / image_processing / ssh / audit_expert / data_expert
-- 全互不干扰。
--
-- 架构决策（2026-07-31）：本地 EAIDE 是单进程 Tauri 桌面应用，**不引入 Redis**。
-- 设计文档 §2.2 里 Redis 承担的「热路径」职责（队列 / 锁 / 令牌桶 / DLQ）在 V1.5
-- 由「进程内 asyncio 结构 + 本表持久化」承担：进程内负责速度，本库负责权威与可回放。

-- ---- 子 Agent 任务（权威状态 + 乐观锁）----------------------------------
CREATE TABLE IF NOT EXISTS sub_agent_tasks (
    task_id            TEXT PRIMARY KEY,          -- = SubAgentSpec.sub_agent_id
    parent_run_id      TEXT NOT NULL,
    parent_task_id     TEXT,
    correlation_id     TEXT NOT NULL,             -- 一棵决策树共享（审计回放键）
    idempotency_token  TEXT NOT NULL,             -- 防重派发
    depth              INTEGER NOT NULL DEFAULT 1,
    task_type          TEXT NOT NULL,
    priority           TEXT NOT NULL DEFAULT 'normal',   -- high / normal / low
    status             TEXT NOT NULL,             -- pending/running/ok/err/dlq/cancelled
    attempts           INTEGER NOT NULL DEFAULT 0,
    state_version      INTEGER NOT NULL DEFAULT 1,-- CAS 乐观锁
    strategy           TEXT,                      -- 实际使用的上下文策略
    local_only         INTEGER NOT NULL DEFAULT 0,-- 敏感负载 → 强制本地
    backend            TEXT,
    tokens_before      INTEGER NOT NULL DEFAULT 0,
    tokens_after       INTEGER NOT NULL DEFAULT 0,
    latency_ms         INTEGER NOT NULL DEFAULT 0,
    spec_json          TEXT NOT NULL,
    report_json        TEXT,
    error              TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sat_idempotency ON sub_agent_tasks(idempotency_token);
CREATE INDEX IF NOT EXISTS idx_sat_correlation ON sub_agent_tasks(correlation_id);
CREATE INDEX IF NOT EXISTS idx_sat_run         ON sub_agent_tasks(parent_run_id);
CREATE INDEX IF NOT EXISTS idx_sat_status      ON sub_agent_tasks(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_sat_parent      ON sub_agent_tasks(parent_task_id);

-- ---- 制品（原文外置 —— 主 Agent 只拿摘要 + 引用）------------------------
CREATE TABLE IF NOT EXISTS sub_agent_artifacts (
    artifact_id   TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'summary',
    content_hash  TEXT NOT NULL DEFAULT '',
    byte_size     INTEGER NOT NULL DEFAULT 0,
    preview       TEXT NOT NULL DEFAULT '',
    uri           TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES sub_agent_tasks(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_saa_task ON sub_agent_artifacts(task_id);
CREATE INDEX IF NOT EXISTS idx_saa_hash ON sub_agent_artifacts(content_hash);

-- ---- 死信队列（3 次重试仍失败 → 运维可 requeue / close）------------------
CREATE TABLE IF NOT EXISTS sub_agent_dlq (
    task_id           TEXT PRIMARY KEY,
    correlation_id    TEXT NOT NULL DEFAULT '',
    idempotency_token TEXT NOT NULL DEFAULT '',
    payload_json      TEXT NOT NULL DEFAULT '{}',
    last_error        TEXT NOT NULL DEFAULT '',
    attempts          INTEGER NOT NULL DEFAULT 0,
    state             TEXT NOT NULL DEFAULT 'open',  -- open / requeued / closed
    note              TEXT,
    handled_by        TEXT,
    enqueued_at       TEXT NOT NULL,
    handled_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_dlq_state ON sub_agent_dlq(state, enqueued_at);
CREATE INDEX IF NOT EXISTS idx_dlq_corr  ON sub_agent_dlq(correlation_id);

-- ---- 评测指标样本（校验通过率 / 压缩率 / Judge 评分）----------------------
CREATE TABLE IF NOT EXISTS sub_agent_metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT,
    correlation_id TEXT,
    metric        TEXT NOT NULL,      -- latency_ms / compression_ratio / judge_score ...
    value         REAL NOT NULL,
    labels_json   TEXT NOT NULL DEFAULT '{}',
    ts            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_metric ON sub_agent_metrics(metric, ts);
CREATE INDEX IF NOT EXISTS idx_metrics_task   ON sub_agent_metrics(task_id);
