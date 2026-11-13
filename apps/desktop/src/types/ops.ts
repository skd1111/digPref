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

// ---------------------------------------------------------------------------
// 系统合规审核报告（2026-11-05）：专家验收前先过一遍「文档审核」模块，
// 有风险点 → 前端弹审核报告（风险点 + 原文证据），无风险不弹窗。
// ---------------------------------------------------------------------------

export type OpsRiskLevel = "low" | "medium" | "high" | "critical";

/** 原文证据定位（与 doc_review Position 对齐） */
export interface OpsRiskPosition {
  page_no: number;
  block_id: string;
  start: number;
  end: number;
}

/** 知识库依据（为什么有风险） */
export interface OpsRiskKbRef {
  source: string;
  heading: string;
  excerpt: string;
  matched_terms: string[];
  file_path: string;
}

export interface OpsRiskFinding {
  finding_id: string;
  risk_type: string;
  risk_level: OpsRiskLevel;
  title: string;
  description: string;
  suggestion: string;
  rule_ref?: string | null;
  /** 风险点对应原文证据（弹窗必展示） */
  evidence_text: string;
  positions?: OpsRiskPosition[];
  kb_refs?: OpsRiskKbRef[];
}

export interface OpsRiskReport {
  /** 审核链路是否跑完（false = 降级，无风险点） */
  audited: boolean;
  degraded: boolean;
  error: string;
  /** document（材料文本/OCR）/ vision（图片视觉描述） */
  basis?: string;
  doc_category: string;
  risk_types?: string[];
  overall_risk_level: OpsRiskLevel | "";
  findings: OpsRiskFinding[];
  elapsed_ms?: number;
  audited_at?: number;
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
  /** 系统合规审核报告（2026-11-05）：有 findings → 前端弹审核报告 */
  risk_report?: OpsRiskReport;
  /** 归属凭证节点名（凭证预览，2026-11-09）：空 = 未归属 */
  credential?: string | undefined;
  /** 归类来源：关键词 / AI / 人工拖动 */
  classified_by?: CredentialClassifiedBy | undefined;
  /** 影像识别轻摘要（块坐标选中时另拉，不随 Case 下发） */
  ocr?: CredentialOcrBrief | undefined;
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
  /** 业务办理台账（业务汇总，2026-11-09）：挂起时前端冻结当前业务界面 */
  task: OpsBusinessTask | null;
}

// ---------------------------------------------------------------------------
// 业务办理台账（业务汇总，2026-11-09，与 agent/ops/tasks.py 对齐）
//
// 与「业务记录卡片」不同：台账是**业务发起即建**、状态随办理流转的一行记录。
// 四态：正在办理 / 挂起 / 人工确认 / 结束。
// ---------------------------------------------------------------------------

export type OpsTaskStatus =
  "in_progress" | "suspended" | "manual_review" | "closed";

export const OPS_TASK_STATUSES: OpsTaskStatus[] = [
  "in_progress",
  "suspended",
  "manual_review",
  "closed",
];

export const OPS_TASK_STATUS_META: Record<
  OpsTaskStatus,
  { label: string; icon: string; color: string }
> = {
  in_progress: { label: "正在办理", icon: "🟦", color: "#0451a5" },
  suspended: { label: "挂起", icon: "🟨", color: "#b25c1a" },
  manual_review: { label: "人工确认", icon: "🟧", color: "#cd3131" },
  closed: { label: "结束", icon: "⬜", color: "#059669" },
};

/** 待人工接入项（被打回 / 审核异常的材料） */
export interface OpsTaskPendingItem {
  file_id: string;
  file_name: string;
  member_key: string;
  kind: "rejected" | "review_error";
  note: string;
}

export interface OpsBusinessTask {
  id: string;
  /** 关联专家验收 Case（项目__功能点） */
  case_id: string;
  project_name: string;
  /** 小类 id（如 corp_open） */
  feature_id: string;
  /** 小类名（如 对公开户辅助） */
  feature_label: string;
  /** 业务大类 id（如 corp_account） */
  category: string;
  /** 业务大类名（如 对公账户管理） */
  category_label: string;
  title: string;
  status: OpsTaskStatus;
  status_label: string;
  status_note: string;
  pending_items: OpsTaskPendingItem[];
  operator: string;
  /** 关联完成后的业务小结卡片 */
  record_id: string;
  created_at: number;
  updated_at: number;
  suspended_at: number;
  closed_at: number;
}

/** 建档参数（业务发起时带上业务大类 / 小类标签） */
export interface OpsTaskStartMeta {
  project_name: string;
  feature_id: string;
  feature_label: string;
  category: string;
  category_label: string;
  title: string;
}

/** 台账查询筛选条件（业务汇总页） */
export interface OpsTaskQuery {
  status?: OpsTaskStatus[] | undefined;
  category?: string | undefined;
  feature_id?: string | undefined;
  project_name?: string | undefined;
  case_id?: string | undefined;
  date_from?: number | undefined;
  date_to?: number | undefined;
  keyword?: string | undefined;
  limit?: number | undefined;
}

/** 台账列表响应（含各状态计数） */
export interface OpsTaskListResult {
  tasks: OpsBusinessTask[];
  total: number;
  counts: Record<OpsTaskStatus, number>;
}

/** 台账详情 */
export interface OpsTaskDetail {
  task: OpsBusinessTask;
  pending_items: OpsTaskPendingItem[];
  file_stats: Record<string, number>;
  file_total: number;
  record: BusinessRecord | null;
  /** 凭证摘要（凭证预览，2026-11-09）：不齐哪几张在汇总页明细行直接可见 */
  credentials?: CredentialStats | undefined;
}

// ---------------------------------------------------------------------------
// 凭证预览（2026-11-09，与 agent/ops/api.py 的 /ops/case/credentials 对齐）
//
// 一次业务办理需要哪几张凭证（专家团 member.credentials 预设）→
// 实传了哪几张（凭证资产）→ 每张采到了哪些要素 →
// 点要素名在影像上放大高亮对应区域（RapidOCR 四点框）。
// ---------------------------------------------------------------------------

/** 归类来源：关键词命中 / AI 判定 / 人工拖动（空 = 尚未归类） */
export type CredentialClassifiedBy = "" | "keyword" | "ai" | "manual";

/** 凭证资产类型：已审材料 / OCR 识别底图 / 录入表单（无影像） */
export type CredentialAssetKind = "file" | "image" | "draft";

/** 影像识别块（RapidOCR 原始四点框，像素坐标） */
export interface OcrBlock {
  text: string;
  /** 四点多边形 [[x,y]×4]（部分引擎返 4 点以上，按 ≥3 点处理） */
  bbox: number[][];
  confidence: number;
}

/** 影像识别轻摘要（聚合响应里只带计数，完整 blocks 选中资产时再拉） */
export interface CredentialOcrBrief {
  engine: string;
  width: number;
  height: number;
  blocks: number;
  confidence: number;
  elapsed_ms: number;
  error: string;
}

/** 完整影像元数据（GET /case/credential-asset/{id} 的 ocr 字段） */
export interface CredentialOcrMeta {
  engine?: string;
  width?: number;
  height?: number;
  blocks?: OcrBlock[];
  text?: string;
  confidence?: number;
  elapsed_ms?: number;
  error?: string;
}

/** 凭证资产（材料行 CF-* 或识别底图行 CI-*） */
export interface CredentialAsset {
  kind: CredentialAssetKind;
  id: string;
  case_id: string;
  team_id: string;
  member_key: string;
  credential: string;
  classified_by: CredentialClassifiedBy;
  file_name: string;
  /** 材料审核状态（pending/reviewing/passed/rejected）；底图为空串 */
  status: string;
  /** upload / ocr-fill / manual */
  source: string;
  draft_id: string;
  review_note: string;
  is_image: boolean;
  has_ocr: boolean;
  ocr: CredentialOcrBrief;
  created_at: number;
}

/** 凭证要素的采集结果（点 label 定位到影像区域） */
export interface CredentialFieldValue {
  name: string;
  label: string;
  value: string;
  /** ai = 审核提取，ocr = 影像识别填入，manual = 人工录入 */
  source: "ai" | "ocr" | "manual";
  asset_id: string;
  asset_kind: CredentialAssetKind;
  asset_credential: string;
  confidence: number;
  /** 来源资产有无块坐标（false = 只能看值，无法定位） */
  can_locate: boolean;
}

/** 单个凭证节点：预设要求 + 实挂资产 + 要素采集情况 */
export interface CredentialNodeView {
  name: string;
  required: boolean;
  media: string;
  hint: string;
  keywords: string[];
  satisfied: boolean;
  assets: CredentialAsset[];
  field_values: CredentialFieldValue[];
  fields_missing: Array<{ name: string; label: string }>;
}

export interface CredentialMemberView {
  member_key: string;
  /** 该专家是否预设了凭证清单（false = 只能看已传材料，判不了缺失） */
  has_preset: boolean;
  credentials: CredentialNodeView[];
  /** 未归属任何节点的资产（可拖到节点上） */
  unassigned: CredentialAsset[];
}

export interface CredentialTeamView {
  team_id: string;
  team_name: string;
  members: CredentialMemberView[];
}

/** 凭证齐备度统计（业务汇总明细行也用同一口径） */
export interface CredentialStats {
  required_total: number;
  required_ok: number;
  missing: Array<{ member_key: string; credential: string }>;
  has_preset: boolean;
  asset_total: number;
  unassigned_total: number;
}

export interface CredentialView {
  case_id: string;
  teams: CredentialTeamView[];
  stats: CredentialStats;
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
