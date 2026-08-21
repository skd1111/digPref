/**
 * SSE / Tauri Event union — discriminated by `kind`.
 */
import type { ChatMessage } from './agent';
import type { ApprovalRequest } from './approval';
import type { TraceStep } from './agent';
import type { ToolCall, ToolResult } from './tools';

export type AgentStreamEvent =
  | { kind: 'message'; message: ChatMessage }
  | { kind: 'tool_call'; id: string; call: ToolCall }
  | { kind: 'tool_result'; id: string; result: ToolResult }
  | { kind: 'trace'; step: TraceStep; /** Phase 16：会话 run_id（思维链查询用） */ runId?: string }
  | { kind: 'approval'; approval: ApprovalRequest }
  | { kind: 'log'; line: string }
  | { kind: 'done'; runId: string }
  | { kind: 'error'; message: string }
  // Phase 1B V1：内置工具执行完成（含 tool_name + ok + result_meta）；
  // 2026-08-19 起前端用它累积 write_file / edit_file 改动文件，任务结束汇总展示
  | { kind: 'builtin_tool_done'; tool_name: string; ok: boolean; result_meta?: Record<string, unknown> }
  // Phase 18 双框架：路由结果 / Auto-Repair 进度 / 自动模式决策
  | { kind: 'mode_routed'; routing: 'coding' | 'work' | 'mixed'; overridden: boolean; declaration?: string | null; runId?: string }
  | { kind: 'repair_attempt'; attempt: number; maxAttempts: number; validatorLevel?: string; errorSummary?: string; runId?: string }
  | { kind: 'auto_decision'; reason: string; option?: string | null; riskLevel?: string; runId?: string };