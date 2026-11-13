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

-- business_tasks：业务办理台账（业务汇总，2026-11-09）。
-- 与 business_records 的区别：那是「办完之后」的小结，本表是「发起即建」、
-- 状态随办理流转的台账（一条业务一行，case_id 唯一）。
-- 状态：in_progress 正在办理 | suspended 挂起 | manual_review 人工确认 | closed 结束
CREATE TABLE IF NOT EXISTS business_tasks (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL DEFAULT '',      -- 关联专家验收 Case（项目__功能点）
    project_name TEXT NOT NULL DEFAULT '',
    feature_id TEXT NOT NULL DEFAULT '',   -- 小类 id（如 corp_open）
    feature_label TEXT NOT NULL DEFAULT '',-- 小类名（如 对公开户辅助）
    category TEXT NOT NULL DEFAULT '',     -- 业务大类 id（如 corp_account）
    category_label TEXT NOT NULL DEFAULT '',-- 业务大类名（如 对公账户管理）
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'in_progress',
    status_note TEXT NOT NULL DEFAULT '',  -- 挂起/人工确认原因
    pending_items TEXT NOT NULL DEFAULT '[]', -- JSON 待处理项 [{file_id,file_name,note,kind}]
    operator TEXT NOT NULL DEFAULT '',     -- 客户经理
    record_id TEXT NOT NULL DEFAULT '',    -- 关联完成后的业务小结卡片
    created_at INTEGER NOT NULL,           -- 发起时间
    updated_at INTEGER NOT NULL,
    suspended_at INTEGER NOT NULL DEFAULT 0,
    closed_at INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_biz_tasks_case ON business_tasks(case_id);
CREATE INDEX IF NOT EXISTS idx_biz_tasks_status ON business_tasks(status);
CREATE INDEX IF NOT EXISTS idx_biz_tasks_category ON business_tasks(category);
CREATE INDEX IF NOT EXISTS idx_biz_tasks_created ON business_tasks(created_at);

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
    risk_report TEXT NOT NULL DEFAULT '{}',        -- JSON：系统合规审核报告 {overall_risk_level,findings[...]原始证据}（2026-11-05）
    credential TEXT NOT NULL DEFAULT '',           -- 归属凭证节点名（空=未归属，2026-11-09 凭证预览）
    classified_by TEXT NOT NULL DEFAULT '',        -- 归类来源：keyword | ai | manual
    ocr_meta TEXT NOT NULL DEFAULT '{}',           -- JSON：端侧影像识别元数据 {engine,width,height,blocks[...四点框]}
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_files_case ON case_files(case_id);

-- credential_images：凭证影像底图（2026-11-09 凭证预览）。
-- 与 case_files 的区别：那是「交给专家审核的材料」，本表是 OCR 自动填入 /
-- 手动补录时留下的识别底图，不走审核链路（避免二次审核噪声），
-- 只用于凭证预览面板「看影像 + 点要素名定位高亮区域」。
CREATE TABLE IF NOT EXISTS credential_images (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    team_id TEXT NOT NULL DEFAULT '',
    member_key TEXT NOT NULL DEFAULT '',
    draft_id TEXT NOT NULL DEFAULT '',             -- 由哪份录入表单的 OCR 产生（可空）
    credential TEXT NOT NULL DEFAULT '',           -- 归属凭证节点名（空=未归属）
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'ocr-fill',       -- 'ocr-fill' | 'manual'
    values_json TEXT NOT NULL DEFAULT '{}',        -- JSON：这张图 OCR 填出了哪些字段值（要素→区域定位用）
    ocr_meta TEXT NOT NULL DEFAULT '{}',           -- JSON：同 case_files.ocr_meta
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credential_images_case ON credential_images(case_id);
CREATE INDEX IF NOT EXISTS idx_credential_images_draft ON credential_images(draft_id);

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
