-- Phase 2F 代码导航索引表
-- 数据库: workspace_index.db (独立 SQLite，与 audit.db 物理隔离)

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,             -- 符号名 (如 calculateInterest)
    kind TEXT NOT NULL,             -- class / method / function / interface / field / enum
    file_path TEXT NOT NULL,        -- 绝对路径
    start_line INTEGER NOT NULL,    -- 起始行号 (1-indexed)
    end_line INTEGER NOT NULL,      -- 结束行号
    signature TEXT,                 -- 完整签名
    parent_class TEXT,              -- 所属类名 (方法/字段专用)
    language TEXT NOT NULL,         -- java / python / typescript / javascript
    last_modified INTEGER NOT NULL  -- os.stat().st_mtime (增量校验)
);

CREATE INDEX IF NOT EXISTS idx_symbol_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_file_path ON symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_language ON symbols(language);
-- 自然唯一键：同一文件同位置不会出现两个不同符号；upsert 依赖此约束
CREATE UNIQUE INDEX IF NOT EXISTS idx_symbol_unique ON symbols(file_path, name, start_line);
