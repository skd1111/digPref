/**
 * opsModules —— Phase 2H 运营工作台左侧「业务列表」静态导航。
 *
 * 16 个一级模块来自用户设计（银行运营智能）。
 * 每个功能点都是现有业务的总结经验，默认以 Skill 形式存在：
 * 选择功能点 → 自动加载绑定 Skill 注入会话上下文；未绑定则引导创建/导入。
 *
 * 标记约定：
 *   - ocr / external：外部系统接入点（OCR 等），V1 留占位
 *   - delegated：由数据专家模式承接（统计报表类，不在运营工作台实现）
 */

export interface OpsNavItem {
  id: string;
  label: string;
  /** 外部系统接入占位（如 'ocr'） */
  external?: "ocr" | "scanner" | "voice" | "sms" | "credit" | "icbc";
  /** 由数据专家模式承接（统计报表/台账） */
  delegated?: boolean;
  /** 默认建议 Skill 名（引导创建时预填） */
  suggestSkill?: string;
}

export interface OpsModule {
  id: string;
  label: string;
  icon: string;
  items: OpsNavItem[];
}

export const OPS_MODULES: OpsModule[] = [
  {
    id: "workbench",
    label: "工作台",
    icon: "🏠",
    items: [
      { id: "today_todos", label: "今日待办", suggestSkill: "工作台-今日待办" },
      {
        id: "risk_reminder",
        label: "风险提醒",
        suggestSkill: "工作台-风险提醒",
      },
      {
        id: "quick_entries",
        label: "快捷入口",
        suggestSkill: "工作台-快捷入口",
      },
      { id: "ai_chat", label: "AI 对话", suggestSkill: "工作台-AI 对话" },
    ],
  },
  {
    id: "biz_process",
    label: "业务办理",
    icon: "🏦",
    items: [
      {
        id: "process_nav",
        label: "业务流程导航",
        suggestSkill: "业务办理-流程导航",
      },
      {
        id: "material_list",
        label: "材料清单生成",
        suggestSkill: "业务办理-材料清单",
      },
      {
        id: "txn_code_query",
        label: "交易码/交易路径查询",
        suggestSkill: "业务办理-交易码查询",
      },
      { id: "faq", label: "常见问题 FAQ", suggestSkill: "业务办理-FAQ" },
      {
        id: "special_guide",
        label: "特殊场景指引",
        suggestSkill: "业务办理-特殊场景",
      },
    ],
  },
  {
    id: "doc_precheck",
    label: "资料预审中心",
    icon: "📑",
    items: [
      {
        id: "doc_upload",
        label: "资料上传",
        external: "scanner",
        suggestSkill: "资料预审-上传",
      },
      {
        id: "image_classify",
        label: "影像自动分类",
        external: "ocr",
        suggestSkill: "资料预审-影像分类",
      },
      {
        id: "completeness",
        label: "资料完整性检查",
        suggestSkill: "资料预审-完整性",
      },
      {
        id: "validity",
        label: "资料有效性检查",
        suggestSkill: "资料预审-有效性",
      },
      {
        id: "consistency",
        label: "资料一致性比对",
        suggestSkill: "资料预审-一致性",
      },
      {
        id: "supplement",
        label: "补件管理",
        external: "sms",
        suggestSkill: "资料预审-补件",
      },
    ],
  },
  {
    id: "smart_form",
    label: "智能填单中心",
    icon: "📝",
    items: [
      {
        id: "ocr_extract",
        label: "OCR 信息提取",
        external: "ocr",
        suggestSkill: "智能填单-OCR",
      },
      { id: "auto_fill", label: "表单自动预填", suggestSkill: "智能填单-预填" },
      {
        id: "field_validate",
        label: "字段校验",
        suggestSkill: "智能填单-字段校验",
      },
      {
        id: "cross_compare",
        label: "多份资料交叉比对",
        suggestSkill: "智能填单-交叉比对",
      },
      {
        id: "template_mgmt",
        label: "填单模板管理",
        suggestSkill: "智能填单-模板",
      },
    ],
  },
  {
    id: "auth_review",
    label: "授权与复核中心",
    icon: "🛡️",
    items: [
      {
        id: "auth_tasks",
        label: "待授权任务",
        suggestSkill: "授权复核-待授权",
      },
      {
        id: "auth_precheck",
        label: "授权前预检",
        suggestSkill: "授权复核-预检",
      },
      {
        id: "review_points",
        label: "复核要点提示",
        suggestSkill: "授权复核-复核要点",
      },
      {
        id: "auth_trace",
        label: "授权记录与追溯",
        suggestSkill: "授权复核-记录追溯",
      },
    ],
  },
  {
    id: "risk_warning",
    label: "风险预警中心",
    icon: "⚠️",
    items: [
      {
        id: "op_risk",
        label: "操作风险预警",
        suggestSkill: "风险预警-操作风险",
      },
      {
        id: "cust_risk",
        label: "客户风险预警",
        suggestSkill: "风险预警-客户风险",
      },
      {
        id: "txn_risk",
        label: "交易风险预警",
        suggestSkill: "风险预警-交易风险",
      },
      {
        id: "warning_flow",
        label: "预警处置流程",
        suggestSkill: "风险预警-处置流程",
      },
    ],
  },
  {
    id: "aml",
    label: "反洗钱辅助分析",
    icon: "🧾",
    items: [
      {
        id: "aml_tasks",
        label: "预警任务列表",
        suggestSkill: "反洗钱-任务列表",
      },
      {
        id: "cust_profile",
        label: "客户画像摘要",
        suggestSkill: "反洗钱-客户画像",
      },
      {
        id: "txn_analysis",
        label: "交易分析",
        suggestSkill: "反洗钱-交易分析",
      },
      {
        id: "suspicious_draft",
        label: "可疑分析初稿",
        suggestSkill: "反洗钱-可疑分析初稿",
      },
      {
        id: "verify_records",
        label: "核实记录管理",
        suggestSkill: "反洗钱-核实记录",
      },
    ],
  },
  {
    id: "corp_account",
    label: "对公账户管理",
    icon: "🏢",
    items: [
      { id: "corp_open", label: "对公开户辅助", suggestSkill: "对公账户-开户" },
      {
        id: "corp_change",
        label: "对公变更辅助",
        suggestSkill: "对公账户-变更",
      },
      {
        id: "corp_close",
        label: "对公销户辅助",
        suggestSkill: "对公账户-销户",
      },
      {
        id: "annual_review",
        label: "账户年检/持续尽调",
        suggestSkill: "对公账户-年检尽调",
      },
      {
        id: "tier_class",
        label: "账户分级分类辅助",
        suggestSkill: "对公账户-分级分类",
      },
    ],
  },
  {
    id: "credit_due",
    label: "信贷尽调",
    icon: "🔍",
    items: [
      {
        id: "credit_materials",
        label: "客户资料收集",
        suggestSkill: "信贷尽调-资料收集",
      },
      {
        id: "ent_basic",
        label: "企业基本信息解析",
        suggestSkill: "信贷尽调-企业解析",
      },
      {
        id: "fin_flow",
        label: "财务与流水分析",
        suggestSkill: "信贷尽调-流水分析",
      },
      {
        id: "credit_summary",
        label: "征信摘要",
        external: "credit",
        suggestSkill: "信贷尽调-征信摘要",
      },
      {
        id: "due_report",
        label: "尽调报告初稿",
        suggestSkill: "信贷尽调-报告初稿",
      },
      {
        id: "site_due",
        label: "现场尽调辅助",
        external: "voice",
        suggestSkill: "信贷尽调-现场尽调",
      },
    ],
  },
  {
    id: "post_loan",
    label: "贷后管理",
    icon: "📊",
    items: [
      {
        id: "loan_tasks",
        label: "贷后任务列表",
        suggestSkill: "贷后管理-任务列表",
      },
      {
        id: "loan_change",
        label: "贷后变化监测",
        suggestSkill: "贷后管理-变化监测",
      },
      {
        id: "loan_report",
        label: "贷后检查报告初稿",
        suggestSkill: "贷后管理-报告初稿",
      },
      {
        id: "risk_advice",
        label: "风险处置建议",
        suggestSkill: "贷后管理-风险处置",
      },
    ],
  },
  {
    id: "cust_marketing",
    label: "客户经营与营销",
    icon: "📣",
    items: [
      { id: "cust_tags", label: "客户标签整理", suggestSkill: "营销-客户标签" },
      { id: "lead_gen", label: "营销线索生成", suggestSkill: "营销-线索生成" },
      {
        id: "talk_script",
        label: "营销话术辅助",
        suggestSkill: "营销-话术辅助",
      },
      {
        id: "visit_assist",
        label: "客户拜访辅助",
        external: "voice",
        suggestSkill: "营销-拜访辅助",
      },
      {
        id: "event_salon",
        label: "活动与沙龙辅助",
        suggestSkill: "营销-活动沙龙",
      },
    ],
  },
  {
    id: "cust_service",
    label: "客户服务与投诉",
    icon: "☎️",
    items: [
      { id: "consult", label: "客户咨询辅助", suggestSkill: "客服-咨询辅助" },
      {
        id: "complaint_summary",
        label: "投诉工单摘要",
        suggestSkill: "客服-投诉摘要",
      },
      {
        id: "complaint_advice",
        label: "投诉处理建议",
        suggestSkill: "客服-投诉处理",
      },
      {
        id: "visit_back",
        label: "回访管理",
        external: "voice",
        suggestSkill: "客服-回访管理",
      },
    ],
  },
  {
    id: "ops_reports",
    label: "运营报表与台账",
    icon: "📈",
    items: [
      { id: "daily_report", label: "日报/周报/月报", delegated: true },
      { id: "ledger", label: "台账管理", delegated: true },
      { id: "stats", label: "数据统计分析", delegated: true },
      { id: "report_export", label: "报表导出与报送", delegated: true },
    ],
  },
  {
    id: "error_rectify",
    label: "差错整改中心",
    icon: "🔧",
    items: [
      {
        id: "error_register",
        label: "差错登记",
        suggestSkill: "差错整改-差错登记",
      },
      {
        id: "error_cause",
        label: "差错原因分析",
        suggestSkill: "差错整改-原因分析",
      },
      {
        id: "rectify_doc",
        label: "整改说明生成",
        suggestSkill: "差错整改-整改说明",
      },
      {
        id: "error_learn",
        label: "差错学习",
        suggestSkill: "差错整改-差错学习",
      },
    ],
  },
  {
    id: "knowledge",
    label: "知识库与培训考试",
    icon: "📚",
    items: [
      {
        id: "kb_policy",
        label: "制度知识库",
        suggestSkill: "知识库-制度知识库",
      },
      { id: "kb_qa", label: "制度问答", suggestSkill: "知识库-制度问答" },
      { id: "kb_train", label: "培训学习", suggestSkill: "知识库-培训学习" },
      { id: "kb_exam", label: "考试测评", suggestSkill: "知识库-考试测评" },
      { id: "kb_map", label: "学习地图", suggestSkill: "知识库-学习地图" },
    ],
  },
  {
    id: "sys_admin",
    label: "系统管理与审计",
    icon: "⚙️",
    items: [
      {
        id: "user_role",
        label: "用户与角色管理",
        suggestSkill: "系统管理-用户角色",
      },
      { id: "perm_ctl", label: "权限控制", suggestSkill: "系统管理-权限控制" },
      {
        id: "model_kb",
        label: "模型与知识库管理",
        suggestSkill: "系统管理-模型知识库",
      },
      { id: "audit_log", label: "日志审计", suggestSkill: "系统管理-日志审计" },
      {
        id: "data_safety",
        label: "数据安全配置",
        suggestSkill: "系统管理-数据安全",
      },
    ],
  },
];

export function findOpsItem(
  itemId: string,
): { module: OpsModule; item: OpsNavItem } | null {
  for (const m of OPS_MODULES) {
    const it = m.items.find((i) => i.id === itemId);
    if (it) return { module: m, item: it };
  }
  return null;
}
