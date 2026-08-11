/**
 * types/reqcard.ts —— 需求改造工作流（需求卡片 V1）类型定义。
 *
 * 与后端 agent/reqflow/models.py 字段一一对应（snake_case 镜像）。
 */

export type CardStatus =
  | 'draft'
  | 'pending_approval'
  | 'approved'
  | 'developing'
  | 'done'
  | 'rejected';

export type CardPriority = 'P0' | 'P1' | 'P2';

export type Feasibility = 'feasible' | 'risky' | 'infeasible';

export interface ReqBatch {
  id: string;
  name: string;
  project_name: string;
  status: 'open' | 'closed';
  created_by: string;
  created_at: number;
  updated_at: number;
}

export interface ReqCard {
  id: string;
  batch_id: string;
  project_name: string;
  system_name: string;
  title: string;
  feature_ids: string[];
  business_value: string;
  change_points: string;
  feasibility: string;
  feasibility_notes: string;
  impact: string;
  external_systems: string[];
  priority: CardPriority;
  status: CardStatus;
  conversation_summary: string;
  session_id: string;
  approved_by: string | null;
  approved_at: number | null;
  version: number;
  created_by: string;
  created_at: number;
  updated_at: number;
}

export interface CardVersionMeta {
  version: number;
  changed_by: string;
  created_at: number;
}

/** 状态元信息：中文标签 + 色标（小卡片展示） */
export const STATUS_META: Record<
  CardStatus,
  { label: string; color: string; icon: string }
> = {
  draft: { label: '草稿', color: '#616161', icon: '⚪' },
  pending_approval: { label: '待审批', color: '#795e26', icon: '🟡' },
  approved: { label: '已批准', color: '#0451a5', icon: '🔵' },
  developing: { label: '开发中', color: '#0b6bcb', icon: '🔵' },
  done: { label: '已完成', color: '#059669', icon: '🟢' },
  rejected: { label: '已驳回', color: '#cd3131', icon: '⛔' },
};

/** 状态流转规则（镜像后端 can_transition；审批预留口在 backend） */
export const STATUS_TRANSITIONS: Record<CardStatus, CardStatus[]> = {
  draft: ['pending_approval', 'rejected'],
  pending_approval: ['approved', 'rejected'],
  approved: ['developing', 'rejected'],
  developing: ['done', 'rejected'],
  done: [],
  rejected: [],
};

export const FEASIBILITY_META: Record<string, { label: string; color: string }> = {
  feasible: { label: '可行', color: '#059669' },
  risky: { label: '有风险', color: '#795e26' },
  infeasible: { label: '不可行', color: '#cd3131' },
};
