/**
 * Skill 类型定义 —— Phase 2D V0 前端独有，不进 shared-protocol。
 * 与 services/agent/src/agent/skills/models.py §3.1 对齐。
 */

export type SkillRisk = 'low' | 'medium' | 'high';
export type FewShotRole = 'user' | 'assistant';

export interface FewShotExample {
  role: FewShotRole;
  content: string;
}

export interface Skill {
  schema_version: string;
  id: string;
  name: string;
  description: string;
  version: string;
  author: string;
  tags: string[];
  risk_level: SkillRisk;
  enabled: boolean;

  trigger_keywords: string[];
  mcp_servers: string[];
  allowed_tools: string[];
  role: string;

  system_prompt: string;
  few_shot_examples: FewShotExample[];

  source_path: string;
  loaded_at: number;
  validation_errors: string[];
}

export interface SkillRoutingResult {
  skill_id: string | null;
  skill_name: string;
  confidence: number;
  matched_keywords: string[];
}

export interface SelectedSkill {
  skill_id: string;
  skill_name: string;
  matched_keywords: string[];
}
