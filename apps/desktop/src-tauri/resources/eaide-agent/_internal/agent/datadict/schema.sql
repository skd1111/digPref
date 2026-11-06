-- dict schema.sql —— 数据字典（Phase 2H）
-- 公共参数独立维护：Skill 中只写「查字典 key」，不内嵌公共参数值。

CREATE TABLE IF NOT EXISTS dict_items (
    key TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT '通用',
    label TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',   -- 'seed' | 'manual'
    updated_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dict_category ON dict_items(category);
CREATE INDEX IF NOT EXISTS idx_dict_label ON dict_items(label);
