/**
 * Tool call / result types.
 */
export type ToolRiskLevel = 'read' | 'low' | 'medium' | 'high' | 'critical';

export interface ToolCall {
  server: string;
  name: string;
  args: Record<string, unknown>;
  riskLevel?: ToolRiskLevel;
  targetSystem?: string;
}

export interface ToolResult {
  server: string;
  name: string;
  ok: boolean;
  data?: unknown;
  error?: string;
  truncated?: boolean;
  rowsReturned?: number;
}