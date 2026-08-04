-- biznav schema.sql —— 业务功能点 4 张表
-- Phase 2G V1.1 (2026-07-28)
-- 所有 id 字段 TEXT；features.version INTEGER 乐观锁；feature_file_index 反向索引
-- 索引上的 WHERE deleted_at IS NULL：避免软删除行污染 category/project 查询

-- features: 功能点主表
CREATE TABLE IF NOT EXISTS features (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL,
    project_name TEXT NOT NULL,
    project_root TEXT NOT NULL,
    related_files TEXT,           -- JSON 数组
    related_apis TEXT,            -- JSON 数组
    related_tables TEXT,          -- JSON 数组
    business_rules TEXT,          -- JSON 数组
    source TEXT NOT NULL DEFAULT 'ai',  -- 'ai' | 'manual' | 'merged'
    ai_confidence REAL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_feature_category ON features(category) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_feature_project ON features(project_name) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_feature_updated ON features(updated_at);

-- feature_file_index: 反向索引（增量更新用）
CREATE TABLE IF NOT EXISTS feature_file_index (
    feature_id TEXT NOT NULL,
    file_path TEXT NOT NULL,        -- 相对 project_root
    PRIMARY KEY (feature_id, file_path),
    FOREIGN KEY (feature_id) REFERENCES features(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_file_index_path ON feature_file_index(file_path);

-- extraction_jobs: 提取任务进度
CREATE TABLE IF NOT EXISTS extraction_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    project_root TEXT NOT NULL,
    status TEXT NOT NULL,           -- 'pending'|'scanning'|'extracting'|'done'|'failed'
    total_files INTEGER DEFAULT 0,
    processed_files INTEGER DEFAULT 0,
    features_generated INTEGER DEFAULT 0,
    error_message TEXT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER
);

-- feature_edit_history: 反馈学习埋点（V1 只写入不导出）
CREATE TABLE IF NOT EXISTS feature_edit_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    edited_at INTEGER NOT NULL,
    editor_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_edit_history_feature ON feature_edit_history(feature_id);
