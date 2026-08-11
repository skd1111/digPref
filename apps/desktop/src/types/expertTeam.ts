/**
 * 专家团类型定义 —— 与后端 agent/expert_teams/models.py 镜像。
 * 专家团是系统一等资产（不以 Skill 形式存在），两级结构：团 + 成员。
 */

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
