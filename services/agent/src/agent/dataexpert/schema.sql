-- Phase 7 V0 · 数据专家模式 3 表（data_expert.db）
-- 与 audit / router / knowledge / ssh / audit_expert 等 db 物理隔离
-- 结果集大对象 → 本地 Parquet 文件（result_data_ref 引用，不入库）

-- 数据源与元数据缓存
CREATE TABLE IF NOT EXISTS data_sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL,              -- 'mysql' / 'oracle' / 'csv' / 'excel'
    connection_ref  TEXT,                       -- Keyring 引用（禁明文，遵 CLAUDE.md §5）
    schema_cache    TEXT,                       -- JSON: 表结构、字段注释、主外键
    updated_at      INTEGER
);

-- 分析任务与 SQL/Python 脚本
CREATE TABLE IF NOT EXISTS analysis_tasks (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    query_sql       TEXT,                       -- 最终执行的 SQL（只读）
    python_script   TEXT,                       -- 数据清洗 Python 脚本
    result_metadata TEXT,                       -- JSON: 列名、数据类型、行数
    result_data_ref TEXT,                       -- 结果集存储引用（本地 Parquet）
    chart_config    TEXT,                       -- JSON: ECharts 配置项
    created_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_analysis_tasks_user ON analysis_tasks(user_id, created_at);

-- 报表模板（可复用的分析逻辑）
CREATE TABLE IF NOT EXISTS report_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    task_id         TEXT NOT NULL,
    schedule_cron   TEXT,                       -- 定时执行 Cron（可选）
    export_format   TEXT,                       -- 'excel' / 'pdf' / 'csv'
    created_by      TEXT,
    is_public       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_report_templates_task ON report_templates(task_id);
