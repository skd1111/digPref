-- loganalysis.schema —— Phase 2F+ V1 日志分析 SQLite schema。
--
-- 物理隔离：与 audit.sqlite / knowledge.db / biznav.db / codenav workspace_index.db /
-- sessions.db / log_index.db（Rust 侧）全互不干扰。
-- Python Agent 侧文件位置：~/.eaide/log_analysis.db（agent 单例管理）。
--
-- 3 张表对应设计文档 §11：
--   - search_cache    : 搜索结果缓存（按 file + pattern + fingerprint 三元组，TTL 1h）
--   - tail_sessions   : tail -f 会话状态（每会话 1 行；last_position + emit 计数）
--   - log_analysis_cache : LLM 根因分析结果缓存（避免重复调 LLM）

CREATE TABLE IF NOT EXISTS search_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    pattern TEXT NOT NULL,
    pattern_type TEXT NOT NULL,           -- 'literal' | 'regex'
    file_fingerprint TEXT NOT NULL,       -- "{size}:{mtime_secs}"
    matched_lines BLOB NOT NULL,          -- u64 LE 编码（与 logviewer/storage.rs::encode_u64_le 对齐）
    match_count INTEGER NOT NULL,
    searched_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_search_cache_key
    ON search_cache(file_path, pattern, pattern_type, file_fingerprint);
CREATE INDEX IF NOT EXISTS idx_search_cache_expires
    ON search_cache(expires_at);

CREATE TABLE IF NOT EXISTS tail_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,      -- uuid
    file_path TEXT NOT NULL,
    last_position INTEGER NOT NULL DEFAULT 0,
    lines_emitted INTEGER NOT NULL DEFAULT 0,
    started_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    ended_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tail_sessions_file
    ON tail_sessions(file_path);
CREATE INDEX IF NOT EXISTS idx_tail_sessions_active
    ON tail_sessions(file_path) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS log_analysis_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT NOT NULL UNIQUE,       -- sha256(file_fingerprint + analysis_type + summary)
    file_path TEXT NOT NULL,
    file_fingerprint TEXT NOT NULL,
    analysis_type TEXT NOT NULL,          -- 'log_root_cause' | 'log_level_classify'
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_log_analysis_cache_expires
    ON log_analysis_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_log_analysis_cache_file
    ON log_analysis_cache(file_path, file_fingerprint);