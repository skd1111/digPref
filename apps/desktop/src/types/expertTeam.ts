/**
 * 专家团类型定义 —— 与后端 agent/expert_teams/models.py 镜像。
 * 专家团是系统一等资产（不以 Skill 形式存在），两级结构：团 + 成员。
 */

/** 凭证要素定义（点要素名 → 在影像上定位高亮） */
export interface ExpertCredentialField {
  /** 字段名（与交付物表单 name 对齐，便于 OCR 填入后回查） */
  name: string;
  /** 展示用中文名 */
  label: string;
}

/**
 * 凭证节点（2026-11-09 凭证预览）：该专家办理时要求上传的影像/单据。
 * keywords 供材料自动归类兜底匹配，fields 声明该凭证期望采集的要素。
 */
export interface ExpertCredential {
  /** 凭证名称（必填，唯一） */
  name: string;
  /** 是否必传 */
  required?: boolean;
  /** 凭证介质：image 影像 / doc 文档 / any 不限 */
  media?: "image" | "doc" | "any";
  /** 上传要求说明（时效、完整性等） */
  hint?: string;
  /** 归类关键词：文件名/识别文本命中即自动挂载 */
  keywords?: string[];
  /** 期望采集的关键要素 */
  fields?: ExpertCredentialField[];
}

export interface ExpertMember {
  /** 专家名称（必填） */
  name: string;
  /** 角色定位（必填） */
  role: string;
  /** 主要职责 */
  responsibilities: string[];
  /** 关注点 */
  focus_points: string[];
  /** 典型输出 */
  outputs: string[];
  /** 交付物 → 表单模板（点交付物直开表单，零 LLM；字段语义与交付草稿模板一致） */
  output_forms?: Record<
    string,
    Array<{
      name: string;
      label: string;
      type?: string;
      options?: string[];
      hint?: string;
      required?: boolean;
    }>
  >;
  /** 凭证清单（凭证预览面板据此展示“该业务要上传哪些图、是否真上传了”） */
  credentials?: ExpertCredential[];
  /** 独立 prompt */
  prompt: string;
}

export interface ExpertTeam {
  schema_version: string;
  id: string;
  name: string;
  description: string;
  /** 适用场景 */
  applicable_scenarios: string[];
  trigger_keywords: string[];
  enabled: boolean;
  members: ExpertMember[];
  /** 交付物报告模板文件名（templates/ 目录下，可选；空 = 自动探测 {id}.docx/.md → 内置结构） */
  report_template: string;

  /** 运行时元数据（后端返回，编辑时不回传也不影响） */
  source_path?: string;
  loaded_at?: number;
}

/** 推荐结果（/expert-teams/recommend） */
export interface ExpertTeamRecommendation {
  team_ids: string[];
  confidence: number;
  reasoning: string;
  /** 'preset' | 'llm' | 'keyword' | 'none' */
  source: string;
}
