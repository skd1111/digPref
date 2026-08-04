-- Phase 6 V0 会话表 schema
-- 数据库：sessions.db（与 audit.db 物理隔离，CLAUDE.md §6）

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT 'default',
    project_name TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived', 'deleted')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    -- Phase 6 V1.5：分支（parent_session_id + branch_from_checkpoint_id + branch_label）
    parent_session_id TEXT DEFAULT NULL,
    branch_from_checkpoint_id TEXT DEFAULT NULL,
    branch_label TEXT NOT NULL DEFAULT '',
    -- Phase 6 V1.5：共享权限矩阵（与 Phase 10 IAM 独立可降级运行）
    share_tokens_json TEXT NOT NULL DEFAULT '[]',
    permissions_json TEXT NOT NULL DEFAULT '{}',
    shared_at INTEGER NOT NULL DEFAULT 0
);

-- V1.5 索引与 ALTER 由 SessionStorage._migrate_v15() 幂等执行（防 schema.sql 重复执行抛 duplicate column）。
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_name);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL
        CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    tool_call_id TEXT,
    tool_name TEXT,
    tool_args_json TEXT,
    tool_result TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON session_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON session_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS session_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (thread_id, checkpoint_id)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON session_checkpoints(session_id);

-- Phase 6 V1.5：会话级事件哈希链（与 audit_expert SHA-256 签名链同等级）。
-- 每次重要事件（创建/分支/共享/导出/压缩应用）→ 计算 prev_hash + payload 的 SHA-256。
-- 验证：prev_hash 与上一条 hash_chain 对齐 → 防篡改。
CREATE TABLE IF NOT EXISTS session_event_chain (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,             -- 'created' | 'branched' | 'shared' | 'exported' | 'compressed' | 'checkpoint' | 'message_appended'
    payload_json TEXT NOT NULL DEFAULT '{}',
    prev_hash TEXT NOT NULL DEFAULT '',  -- 上一条 hash（首条为 '0'*64）
    hash TEXT NOT NULL,                  -- sha256(prev_hash + event_type + payload_json + created_at)
    actor TEXT NOT NULL DEFAULT 'system',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_chain_session ON session_event_chain(session_id, id);
CREATE INDEX IF NOT EXISTS idx_event_chain_type ON session_event_chain(event_type);

-- Phase 6 V1.5：FTS5 全文搜索虚拟表（覆盖会话标题 + 消息内容 + 工具调用结果）。
-- 使用 contentless + external content 模式：源数据仍在 sessions / session_messages，FTS 仅存索引。
-- 触发器：INSERT / UPDATE / DELETE 时自动同步 FTS 索引。
CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    session_id UNINDEXED,
    title,
    content,
    tool_name,
    tool_result,
    created_at UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- 同步触发器：INSERT session → FTS
CREATE TRIGGER IF NOT EXISTS sessions_ai AFTER INSERT ON sessions
BEGIN
    INSERT INTO sessions_fts(session_id, title, content, tool_name, tool_result, created_at)
    VALUES (NEW.id, NEW.title, '', '', '', NEW.created_at);
END;

-- 同步触发器：UPDATE session.title → FTS
CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE OF title ON sessions
BEGIN
    UPDATE sessions_fts SET title = NEW.title WHERE session_id = NEW.id;
END;

-- 同步触发器：DELETE session → FTS (CASCADE 一起删 messages 也会触发下面的 trigger)
CREATE TRIGGER IF NOT EXISTS sessions_ad AFTER DELETE ON sessions
BEGIN
    DELETE FROM sessions_fts WHERE session_id = OLD.id;
END;

-- 同步触发器：INSERT message → FTS
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON session_messages
BEGIN
    INSERT INTO sessions_fts(session_id, title, content, tool_name, tool_result, created_at)
    SELECT NEW.session_id, COALESCE((SELECT title FROM sessions WHERE id = NEW.session_id), ''),
           NEW.content, COALESCE(NEW.tool_name, ''), COALESCE(NEW.tool_result, ''),
           NEW.created_at;
END;

-- 同步触发器：DELETE message → FTS
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON session_messages
BEGIN
    DELETE FROM sessions_fts
    WHERE session_id = OLD.session_id
      AND content = OLD.content
      AND created_at = OLD.created_at
      AND rowid = (SELECT MAX(rowid) FROM sessions_fts
                   WHERE session_id = OLD.session_id AND content = OLD.content);
END;

-- Phase 6 V1 MACC：L3 Semantic Memory（语义规则蒸馏表）
CREATE TABLE IF NOT EXISTS semantic_rules (
    id TEXT PRIMARY KEY,                -- uuid
    session_id TEXT NOT NULL,           -- 来源会话（最近一次更新）
    pattern TEXT NOT NULL,              -- 触发模式（如 "订单平账" / "deploy redis"）
    rule_text TEXT NOT NULL,            -- 蒸馏后的规则描述（自然语言）
    confidence REAL NOT NULL DEFAULT 0.0,  -- 0-1（出现频率 / 总数归一化）
    last_updated INTEGER NOT NULL,
    source_event_ids_json TEXT NOT NULL DEFAULT '[]',  -- JSON 数组：可追溯的 source events
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_semantic_rules_pattern ON semantic_rules(pattern);
CREATE INDEX IF NOT EXISTS idx_semantic_rules_session ON semantic_rules(session_id);
CREATE INDEX IF NOT EXISTS idx_semantic_rules_updated ON semantic_rules(last_updated DESC);

-- Phase 6 V1 MACC：L3 Episodic Memory（事件图谱节点）
CREATE TABLE IF NOT EXISTS event_graph_nodes (
    id TEXT PRIMARY KEY,                -- uuid
    session_id TEXT NOT NULL,
    entity TEXT NOT NULL,               -- 主体（如 "orders_db.orders" / "hitl_gate" / "tool_runner"）
    action TEXT NOT NULL,               -- 动作（如 "SELECT count(*)" / "approve" / "UPDATE status"）
    result TEXT NOT NULL DEFAULT '',    -- 结果摘要
    status TEXT NOT NULL DEFAULT 'ok',  -- 'ok' | 'pending' | 'rejected' | 'error'
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_nodes_session ON event_graph_nodes(session_id);
CREATE INDEX IF NOT EXISTS idx_event_nodes_entity ON event_graph_nodes(entity);
CREATE INDEX IF NOT EXISTS idx_event_nodes_created ON event_graph_nodes(created_at DESC);

-- Phase 6 V1 MACC：L3 Episodic Memory（事件图谱边）
CREATE TABLE IF NOT EXISTS event_graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'next',  -- 'next' | 'triggers' | 'depends_on' | 'caused_by'
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (from_node) REFERENCES event_graph_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (to_node) REFERENCES event_graph_nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_edges_session ON event_graph_edges(session_id);
CREATE INDEX IF NOT EXISTS idx_event_edges_from ON event_graph_edges(from_node);
CREATE INDEX IF NOT EXISTS idx_event_edges_to ON event_graph_edges(to_node);

-- Phase 6 V1 MACC：压缩日志（可观测性 + 未来 PPO 训练数据收集）
CREATE TABLE IF NOT EXISTS compression_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    strategy TEXT NOT NULL,             -- 'KV_CACHE' | 'GIST' | 'MEMORY' | 'HYBRID' | 'NONE'
    before_tokens INTEGER NOT NULL,
    after_tokens INTEGER NOT NULL,
    compression_ratio REAL NOT NULL,     -- after / before
    layers_used_json TEXT NOT NULL DEFAULT '[]',  -- JSON: ["L1", "L2", "L3"]
    elapsed_ms INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_compression_log_session ON compression_log(session_id);
CREATE INDEX IF NOT EXISTS idx_compression_log_created ON compression_log(created_at DESC);