/**
 * HITL approval types — exchanged between Agent (pause) and Desktop UI (decision).
 */
import type { ToolCall, ToolRiskLevel } from './tools';

/**
 * Phase 18: approval candidate option (Work framework recommended-option mechanism).
 */
export interface ApprovalOption {
  id: string;
  label: string;
  adjustedPlan: string;
  riskNote?: string | null;
}

export interface ApprovalRequest {
  id: string;
  runId: string;
  plan: ToolCall;
  riskLevel: ToolRiskLevel;
  reason?: string;
  createdAt: string;
  // Phase 18 dual-framework: recommended options (empty = binary approve/reject, backward compatible)
  options?: ApprovalOption[];
  recommendedOptionId?: string | null;
  recommendationReason?: string | null;
}

/**
 * approve_always（2026-08-25）：批准且本会话内同工具（同 server·name）
 * 后续操作自动放行；硬阻断（DROP/TRUNCATE）任何决策都不可豁免。
 */
export type ApprovalDecision = 'approve' | 'reject' | 'approve_always';

export interface PendingApproval extends ApprovalRequest {
  // mirrors ApprovalRequest; kept distinct so it can evolve separately.
}