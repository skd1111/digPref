-- 需求改造工作流（reqflow V1）—— reqcards.db
-- 与 audit.sqlite / biznav.db / router.db 物理隔离。
-- 由 reqflow/storage.py 首次访问时 executescript() 自动建表。

-- 需求批次
CREATE TABLE IF NOT EXISTS req_batches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- open / closed
    created_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 需求卡片（当前最新版；历史版本快照在 req_card_versions）
CREATE TABLE IF NOT EXISTS req_cards (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES req_batches(id),
    project_name TEXT NOT NULL,
    system_name TEXT NOT NULL,
    title TEXT NOT NULL,
    feature_ids TEXT NOT NULL DEFAULT '[]',        -- JSON array
    business_value TEXT NOT NULL DEFAULT '',
    change_points TEXT NOT NULL DEFAULT '',
    feasibility TEXT NOT NULL DEFAULT '',          -- feasible / risky / infeasible
    feasibility_notes TEXT NOT NULL DEFAULT '',
    impact TEXT NOT NULL DEFAULT '',
    external_systems TEXT NOT NULL DEFAULT '[]',   -- JSON array
    priority TEXT NOT NULL DEFAULT 'P2',           -- P0 / P1 / P2
    status TEXT NOT NULL DEFAULT 'draft',
    conversation_summary TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    approved_by TEXT,
    approved_at INTEGER,
    version INTEGER NOT NULL DEFAULT 1,            -- 每次修改 +1
    created_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_req_cards_batch ON req_cards(batch_id);
CREATE INDEX IF NOT EXISTS idx_req_cards_project ON req_cards(project_name);

-- 卡片历史版本快照（修改前整卡 JSON 存档，只读）
CREATE TABLE IF NOT EXISTS req_card_versions (
    card_id TEXT NOT NULL REFERENCES req_cards(id),
    version INTEGER NOT NULL,
    snapshot TEXT NOT NULL,                        -- 旧版本整卡 JSON
    changed_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    PRIMARY KEY (card_id, version)
);
