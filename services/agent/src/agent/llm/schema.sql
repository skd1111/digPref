-- 智能路由数据层（Phase 2C v2）—— router.db
-- 与 audit.sqlite / workspace_index.db / biznav.db / knowledge.db 物理隔离，
-- 避免文件锁竞争。由 llm/storage.py 在首次访问时 executescript() 自动建表。
-- 单一真源：storage.py 读取本文件，不再内联 SQL。

-- 模型后端配置
CREATE TABLE IF NOT EXISTS llm_backends (
    name TEXT PRIMARY KEY,
    type TEXT NOT NULL,              -- 'local' / 'private' / 'cloud'
    base_url TEXT NOT NULL,
    api_key_ref TEXT,               -- API Key 明文直存（配置文件模式，不走系统凭据管理器）
    model_name TEXT NOT NULL,
    capabilities TEXT,              -- JSON array
    max_context INTEGER,
    cost_per_1k_tokens REAL,
    timeout_seconds INTEGER DEFAULT 30,
    data_residency TEXT,            -- 'local' / 'private' / 'cloud'
    enabled INTEGER DEFAULT 1,
    role TEXT NOT NULL DEFAULT 'execution'  -- Phase 2C V2.5: utility / reasoning / execution
);

-- 路由决策日志（全链路 Trace）
CREATE TABLE IF NOT EXISTS routing_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    task_category TEXT,
    sensitivity TEXT,
    primary_backend TEXT,
    actual_backend TEXT,
    fallback_used INTEGER,
    cache_hit INTEGER,
    estimated_cost REAL,
    actual_cost REAL,
    latency_ms INTEGER,
    quality_score REAL,
    trace_json TEXT,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rd_request ON routing_decisions(request_id);
CREATE INDEX IF NOT EXISTS idx_rd_user ON routing_decisions(user_id);
CREATE INDEX IF NOT EXISTS idx_rd_created ON routing_decisions(created_at);

-- 成本统计（按日聚合）
CREATE TABLE IF NOT EXISTS cost_daily (
    date TEXT,
    user_id TEXT,
    backend TEXT,
    task_category TEXT,
    total_tokens INTEGER,
    total_cost REAL,
    call_count INTEGER,
    PRIMARY KEY (date, user_id, backend, task_category)
);

-- 功能 → backend 绑定（Phase 2F）
-- 每个功能键挑一个 llm_backends.name；NULL = 未选（走 mock 或环境变量）
CREATE TABLE IF NOT EXISTS feature_backend (
    feature TEXT PRIMARY KEY,       -- 'codenav' / 'planner' / 'intent' / ...
    backend_name TEXT,              -- llm_backends.name（外键语义；不强制约束以允许删除后端）
    updated_at INTEGER
);

-- 评分权重（Phase 2C V2）—— 单行 id=1；前端 ScoringWeightsEditor 编辑后 PUT 落库
-- 启动时 RouterEngine.__init__ 读取，作为 scoring.score_backend 的权重
CREATE TABLE IF NOT EXISTS router_weights (
    id INTEGER PRIMARY KEY CHECK (id = 1),   -- 强制单行（避免误 INSERT 多行）
    capability REAL NOT NULL DEFAULT 0.35,
    cost REAL NOT NULL DEFAULT 0.25,
    latency REAL NOT NULL DEFAULT 0.20,
    compliance REAL NOT NULL DEFAULT 0.15,
    availability REAL NOT NULL DEFAULT 0.05,
    updated_at INTEGER NOT NULL
);
