/**
 * SSE / Tauri Event union — discriminated by `kind`.
 */
import type { ChatMessage } from './agent';
import type { ApprovalRequest } from './approval';
import type { TraceStep } from './agent';
import type { ToolCall, ToolResult } from './tools';

export type AgentStreamEvent =
  | { kind: 'message'; message: ChatMessage }
  // 多会话并发（2026-08-26）：runId 标识所属 run，前端按 runId→页签路由不串台；
  // 旧后端不带时为 undefined，回退当前激活页签（兼容）
  // callId（根治 BUGFIX #164）：tool_call 与 tool_result 共享的调用标识，
  // 前端据此把 running 卡片翻成 ok/err。此前两条事件各自 uuid4 无从配对，
  // 卡片永久转圈。旧后端不带时为 undefined，前端回退按 name 匹配。
  | { kind: 'tool_call'; id: string; callId?: string; call: ToolCall; runId?: string }
  | { kind: 'tool_result'; id: string; callId?: string; result: ToolResult; runId?: string }
  | { kind: 'trace'; step: TraceStep; /** Phase 16：会话 run_id（思维链查询用） */ runId?: string }
  | { kind: 'approval'; approval: ApprovalRequest; runId?: string }
  | { kind: 'log'; line: string }
  | { kind: 'done'; runId: string; /** 任务级工作目录（2026-08-26）：本轮任务文件夹的 id 与绝对路径 */ taskId?: string; taskDir?: string }
  | { kind: 'error'; message: string; runId?: string }
  // 流保活心跳（BUGFIX #161）：后端每 15s 无图块时下发；前端看门狗据此感知流存活，
  // 静默超阈判定断连并主动解锁，防 SSE 静默断连后永久卡「思考中」
  | { kind: 'heartbeat'; runId?: string }
  // Phase 2D V0：skill 路由命中（2026-08-26 起前端记录 lastSkillId 供追问轮继承）
  | { kind: 'skill_matched'; skill_id: string; skill_name: string; confidence?: number; runId?: string }
  // Phase 1B V1：内置工具执行完成（含 tool_name + ok + result_meta）；
  // 2026-08-19 起前端用它累积 write_file / edit_file 改动文件，任务结束汇总展示；
  // call_id（执行过程可视化·阶段四）：前端据此把对应的写前预览卡翻牌成终态。
  | { kind: 'builtin_tool_done'; tool_name: string; ok: boolean; call_id?: string; result_meta?: Record<string, unknown>; runId?: string }
  // 2026-08-26：内置工具开始执行（前端把「思考中」细化为「工具调用中：某动作」）
  | { kind: 'builtin_tool_started'; tool_name: string; risk_level?: string; needs_hitl?: boolean; runId?: string }
  // Phase 18 双框架：路由结果 / Auto-Repair 进度 / 自动模式决策
  | { kind: 'mode_routed'; routing: 'coding' | 'work' | 'mixed'; overridden: boolean; declaration?: string | null; runId?: string }
  | { kind: 'repair_attempt'; attempt: number; maxAttempts: number; validatorLevel?: string; errorSummary?: string; runId?: string }
  | { kind: 'auto_decision'; reason: string; option?: string | null; riskLevel?: string; runId?: string }
  // 执行过程可视化（Claude Code 式）：run 显式开始 / 工具进度 / shell 流式输出 / 写前 Diff 预览。
  // callId 与 tool_call/tool_result 同源（BUGFIX #164 配对键），前端按它归并到对应工具卡。
  | { kind: 'run_started'; runId?: string }
  | { kind: 'tool_progress'; call_id?: string; callId?: string; tool_name?: string; message?: string; percent?: number; runId?: string }
  | { kind: 'shell_chunk'; call_id?: string; callId?: string; stream: 'stdout' | 'stderr'; chunk: string; exit_code?: number; runId?: string }
  | { kind: 'file_write_preview'; call_id?: string; callId?: string; path: string; diff: string; risk_level?: string; runId?: string };