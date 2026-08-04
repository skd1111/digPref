-- 文档风险合规审核（审核专家 · 文档审核）doc_review.db
-- 与 audit_expert / preview / audit 等 db 物理隔离
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL UNIQUE,
  file_name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  format TEXT NOT NULL,
  page_count INTEGER NOT NULL,
  pages_json TEXT NOT NULL,
  full_text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_docs_id ON documents(doc_id);

CREATE TABLE IF NOT EXISTS analysis_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  doc_id TEXT NOT NULL,
  status TEXT NOT NULL,
  doc_category TEXT,
  risk_types_json TEXT,
  overall_risk_level TEXT,
  summary TEXT,
  model_provider TEXT,
  model_name TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_runs_doc ON analysis_runs(doc_id, status);

CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  risk_type TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  suggestion TEXT,
  rule_ref TEXT,
  evidence_text TEXT,
  positions_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_findings_doc ON findings(doc_id, risk_level);
