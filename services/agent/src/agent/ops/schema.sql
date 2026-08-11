-- ops schema.sql —— 业务记录卡片（Phase 2H）
-- 每笔业务完成后由 AI 生成的可审计小结，供运营工作台展示与事后检查。

CREATE TABLE IF NOT EXISTS business_records (
    id TEXT PRIMARY KEY,
    project_name TEXT NOT NULL DEFAULT '',
    feature_id TEXT NOT NULL DEFAULT '',
    business_type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    materials_checked TEXT NOT NULL DEFAULT '[]',   -- JSON 数组
    materials_missing TEXT NOT NULL DEFAULT '[]',   -- JSON 数组
    risk_points TEXT NOT NULL DEFAULT '[]',         -- JSON 数组
    result TEXT NOT NULL DEFAULT 'done',            -- done|pending|rejected|follow_up
    skill_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'ai',              -- 'ai' | 'manual'
    created_by TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_biz_records_feature ON business_records(feature_id);
CREATE INDEX IF NOT EXISTS idx_biz_records_project ON business_records(project_name);
CREATE INDEX IF NOT EXISTS idx_biz_records_created ON business_records(created_at);

-- 专家验收工作流（2026-08-10）：一次业务办理 = 一个 Case。
-- case_files：客户经理上传给对应专家的材料 + AI/人工验收结果。
CREATE TABLE IF NOT EXISTS case_files (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    team_id TEXT NOT NULL DEFAULT '',
    member_key TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',        -- pending|reviewing|passed|rejected
    review_note TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT NOT NULL DEFAULT '',          -- 'ai' | 'human' | ''
    extracted_fields TEXT NOT NULL DEFAULT '[]',   -- JSON：关键要素 [{field,value,confidence}]（低置信前端标红）
    evidence TEXT NOT NULL DEFAULT '[]',           -- JSON：审核依据原文摘录（证据链，防黑盒）
    reject_marks TEXT NOT NULL DEFAULT '[]',       -- JSON：打回定位 [{quote,advice}]（文档内高亮，BUGFIX #80）
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_files_case ON case_files(case_id);

-- case_corrections：人工纠错样本（铁律 2，2026-08-10）。
-- AI 结论被人工改判时记录「原材料 + AI 结论 + 人工结论」，作为后续提示词/模型改进的数据源。
CREATE TABLE IF NOT EXISTS case_corrections (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    member_key TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL DEFAULT '',
    ai_status TEXT NOT NULL DEFAULT '',
    ai_note TEXT NOT NULL DEFAULT '',
    human_status TEXT NOT NULL DEFAULT '',
    human_note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_corrections_case ON case_corrections(case_id);

-- case_qa：客户经理向专家的提问与回答（迷你问答，取代大 Chat）。
CREATE TABLE IF NOT EXISTS case_qa (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    member_key TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_qa_case ON case_qa(case_id);

-- case_drafts：交付草稿（界面直填，BUGFIX #78）。
-- 用户向专家要「清单/模板」时，不输出一大段文字，而是生成结构化表单；
-- 用户在界面直接填写 → 提交后自动成为材料走专家审核 → 通过后并入交付物 zip。
CREATE TABLE IF NOT EXISTS case_drafts (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    team_id TEXT NOT NULL DEFAULT '',
    member_key TEXT NOT NULL DEFAULT '',         -- 出题专家（提交后材料归属）
    title TEXT NOT NULL DEFAULT '',
    template_json TEXT NOT NULL DEFAULT '{}',    -- 字段定义 [{name,label,type,options,hint,required}]
    values_json TEXT NOT NULL DEFAULT '{}',      -- 用户填写值 {name: value}
    status TEXT NOT NULL DEFAULT 'draft',        -- draft|submitted|passed
    file_id TEXT NOT NULL DEFAULT '',            -- 提交后生成的 case_files 行 id
    last_snapshot TEXT NOT NULL DEFAULT '',      -- 上次提交渲染的 md 快照（打回后对照修改，BUGFIX #79）
    submit_count INTEGER NOT NULL DEFAULT 0,     -- 提交次数（重提版本计数）
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_drafts_case ON case_drafts(case_id);

-- case_sessions：Case → 会话管理归档映射（2026-08-11）。
-- 专家问答同步写入 sessions（会话管理可见），每 Case 一个会话；归档失败不影响问答主链路。
CREATE TABLE IF NOT EXISTS case_sessions (
    case_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
);
