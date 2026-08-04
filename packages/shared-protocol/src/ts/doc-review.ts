/**
 * 文档风险合规审核契约 — TS 镜像（snake_case，与 FastAPI 响应一致，参照 dspark.ts 先例）。
 * Spec: docs/superpowers/specs/2026-08-04-doc-risk-review-design.md
 */

export type DocFormat = 'pdf' | 'docx' | 'txt' | 'md';
export type DocCategory =
  | 'contract'
  | 'internal_policy'
  | 'announcement'
  | 'bidding'
  | 'other';
export type DocRiskType = 'compliance' | 'legal' | 'data_security' | 'financial';
export type DocRiskLevel = 'low' | 'medium' | 'high' | 'critical';
export type DocRunStatus = 'queued' | 'classifying' | 'analyzing' | 'done' | 'failed';

export interface DocBlock {
  block_id: string;
  text: string;
  start: number;
  end: number;
}

export interface DocPage {
  page_no: number;
  blocks: DocBlock[];
}

export interface DocPosition {
  page_no: number;
  block_id: string;
  start: number;
  end: number;
}

export interface DocFinding {
  finding_id: string;
  risk_type: DocRiskType;
  risk_level: DocRiskLevel;
  title: string;
  description: string;
  suggestion: string;
  rule_ref: string | null;
  evidence_text: string;
  positions: DocPosition[];
}

export interface DocSummary {
  doc_id: string;
  file_name: string;
  format: DocFormat;
  page_count: number;
  status: DocRunStatus | 'none';
  overall_risk_level: DocRiskLevel | null;
  created_at: string;
}

export interface DocDetail extends DocSummary {
  file_path: string;
  doc_category: DocCategory | null;
  risk_types: DocRiskType[];
  summary: string | null;
  pages: DocPage[];
  findings: DocFinding[];
}
