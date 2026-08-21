/**
 * Agent message + trace types.
 */
import type { ApprovalRequest } from './approval';

export type ChatRole = 'user' | 'assistant' | 'tool' | 'system';

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  code?: string;
  codeLang?: 'sql' | 'json' | 'python' | 'bash';
  pendingApproval?: ApprovalRequest;
  /**
   * execution = Codex/Claude 风格的执行链路块（role='system' + kind='execution'）。
   * 渲染为可折叠的灰色 step block（intent / plan / tool_call / tool_result / repair / summarise / explain）。
   * error = 流异常终止的系统消息（2026-08-07），前端渲染「重试」按钮。
   * search = 搜索/检索类工具调用（2026-08-10），前端渲染 aicss 风格搜索卡片。
   * changed_files = 任务结束汇总的改动文件清单（2026-08-19），content 为路径 JSON 数组，
   *   前端渲染可点击卡片，点击在 Monaco 打开对应文件。
   */
  kind?: 'execution' | 'normal' | 'error' | 'search' | 'changed_files';
  /** execution kind 时附带的分类标签 + 耗时（前端用来着色） */
  category?: string;
  latencyMs?: number;
  status?: 'running' | 'ok' | 'err';
  /** 关联的 run_id（让 UI 能把同一 run 的所有 step 折叠到一个块里） */
  runId?: string;
}

export type AgentRunStatus = 'idle' | 'running' | 'awaiting_approval' | 'done' | 'error' | 'cancelled';

export interface AgentRun {
  runId: string;
  status: AgentRunStatus;
  startedAt: string;
  finishedAt?: string;
  error?: string;
}

export interface TraceStep {
  id: string;
  node: 'intent' | 'planner' | 'tool_runner' | 'hitl_gate' | 'repair' | 'responder' | string;
  status: 'ok' | 'fail' | 'running' | 'skipped';
  durationMs: number;
  summary?: string;
  /** 错误详情（repair / tool_runner fail 时） */
  error?: string;
  /** HITL 审批 ID */
  approvalId?: string;
  /** 审批决策：approve / reject */
  decision?: string;
  /** 重试次数 */
  attempts?: number;
  /** 工具名称 */
  toolName?: string;
  /** LLM 规划的说明文本 */
  rationale?: string;
}