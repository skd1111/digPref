/**
 * ops —— Phase 2H 运营工作台业务记录类型（前端独有，与 agent/ops/models.py 对齐）。
 *
 * 业务记录卡片：做完一笔业务后 AI 生成的总结，可审计、可统计。
 * 统计报表由数据专家模式承接，这里不做。
 */

export type OpsRecordResult = "done" | "pending" | "rejected" | "follow_up";

export interface BusinessRecord {
  id: string;
  project_name: string;
  feature_id: string;
  business_type: string;
  title: string;
  summary: string;
  materials_checked: string[];
  materials_missing: string[];
  risk_points: string[];
  result: OpsRecordResult;
  skill_id: string;
  session_id: string;
  source: "ai" | "manual";
  created_by: string;
  created_at: number;
  updated_at: number;
}

export const OPS_RESULT_META: Record<
  OpsRecordResult,
  { label: string; icon: string; color: string }
> = {
  done: { label: "已完成", icon: "✅", color: "#059669" },
  pending: { label: "进行中", icon: "⏳", color: "#b25c1a" },
  rejected: { label: "未受理", icon: "⛔", color: "#cd3131" },
  follow_up: { label: "需跟进", icon: "🔁", color: "#795e26" },
};

export interface OpsRecordDraft {
  title: string;
  business_type: string;
  summary: string;
  materials_checked: string[];
  materials_missing: string[];
  risk_points: string[];
  result: OpsRecordResult;
}

// ---------------------------------------------------------------------------
// 专家验收工作流 Case（2026-08-10，与 agent/ops/cases.py 对齐）
// 一次业务办理 = 一个 Case：材料上传 → AI 专家审核 → 迷你问答 → 打包导出
// ---------------------------------------------------------------------------

export type CaseFileStatus = "pending" | "reviewing" | "passed" | "rejected";

/** AI 提取的关键要素（confidence < 0.6 为低置信，前端标红提醒人工核对） */
export interface OpsCaseField {
  field: string;
  value: string;
  confidence: number;
}

/** 打回定位（BUGFIX #80）：问题在原文中的位置 + 修改建议，预览时高亮 */
export interface OpsRejectMark {
  quote: string;
  advice: string;
}

export interface OpsCaseFile {
  id: string;
  case_id: string;
  team_id: string;
  member_key: string;
  file_name: string;
  file_path: string;
  status: CaseFileStatus;
  review_note: string;
  reviewed_by: "ai" | "human" | "";
  /** 关键要素提取结果（2026-08-10 厚资料变薄数据） */
  extracted_fields?: OpsCaseField[];
  /** 审核依据原文摘录（证据链，防黑盒不信任） */
  evidence?: string[];
  /** 打回定位：问题在原文中的位置（文档内高亮，BUGFIX #80） */
  reject_marks?: OpsRejectMark[];
  created_at: number;
  updated_at: number;
}

export interface OpsCaseQa {
  id: string;
  case_id: string;
  member_key: string;
  question: string;
  answer: string;
  created_at: number;
}

// ---------------------------------------------------------------------------
// 交付草稿（BUGFIX #78：要「模板/清单」不给一大段文字，界面直填）
// 填写 → 提交 → 自动成为材料走专家审核 → 通过即并入交付物 zip
// ---------------------------------------------------------------------------

export type DraftFieldType = "text" | "textarea" | "select" | "date" | "file";

export interface OpsDraftField {
  name: string;
  label: string;
  type: DraftFieldType;
  options?: string[];
  hint?: string;
  required?: boolean;
}

export type OpsDraftStatus = "draft" | "submitted" | "passed";

export interface OpsCaseDraft {
  id: string;
  case_id: string;
  team_id: string;
  member_key: string;
  title: string;
  template: OpsDraftField[];
  values: Record<string, string>;
  status: OpsDraftStatus;
  /** 提交后生成的材料行 id（审核意见在 files 里看） */
  file_id: string;
  /** 上次提交渲染的 md 快照（打回后对照修改，BUGFIX #79） */
  last_snapshot?: string;
  /** 提交次数（重提版本计数） */
  submit_count?: number;
  created_at: number;
  updated_at: number;
}

export interface OpsCaseState {
  case_id: string;
  files: OpsCaseFile[];
  qa: OpsCaseQa[];
  drafts: OpsCaseDraft[];
}

export const CASE_FILE_STATUS_META: Record<
  CaseFileStatus,
  { label: string; icon: string; color: string }
> = {
  pending: { label: "待审核", icon: "○", color: "#795e26" },
  reviewing: { label: "审核中", icon: "⏳", color: "#0451a5" },
  passed: { label: "通过", icon: "✓", color: "#059669" },
  rejected: { label: "打回", icon: "✗", color: "#cd3131" },
};
