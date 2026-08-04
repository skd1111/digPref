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

export type ApprovalDecision = 'approve' | 'reject';

export interface PendingApproval extends ApprovalRequest {
  // mirrors ApprovalRequest; kept distinct so it can evolve separately.
}