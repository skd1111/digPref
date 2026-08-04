/**
 * Phase 16 · 思维链可视化与文件操作追踪 —— 线协议类型。
 *
 * 与 Python 侧 services/agent/src/agent/trace/models.py 严格镜像：
 *   - FileOperation  ←→  agent.trace.models.FileOperation
 *   - ThinkingStep   ←→  agent.trace.models.ThinkingStep
 */

/** 文件操作类型（与 Python OP_* 常量一致） */
export type FileOpType = 'read' | 'write' | 'edit' | 'grep' | 'reference';

export interface FileOperation {
  type: FileOpType;
  /** 文件路径（沙箱校验后的绝对路径） */
  path: string;
  /** unified diff（read/grep 为 null） */
  diff: string | null;
  /** diff 关键片段（前后 50 行） */
  preview: string | null;
  /** + 行数统计 */
  lines_added: number;
  /** - 行数统计 */
  lines_removed: number;
  start_line: number | null;
  end_line: number | null;
  ok: boolean;
  error: string | null;
}

export interface ToolCallRecord {
  name: string;
  server?: string | null;
  params?: Record<string, unknown>;
  risk_level?: string | null;
  result?: {
    ok: boolean;
    content?: string;
    error?: string | null;
  };
}

export interface ThinkingStep {
  id: string;
  session_id: string;
  message_id: string | null;
  step_index: number;
  /** LangGraph 节点名（intent / planner / tool_runner / responder ...） */
  node_name: string;
  /** 中文思考内容（【思考】/【行动】/【观察】/【决策】四段式） */
  thinking: string | null;
  thinking_tokens: number | null;
  tool_calls: ToolCallRecord[];
  file_operations: FileOperation[];
  /** 【决策】最终结论 */
  decision: string | null;
  tokens_used: number | null;
  latency_ms: number | null;
  /** epoch 毫秒 */
  created_at: number;
}

/** GET /trace/session/{id} 响应 */
export interface TraceSessionResponse {
  session_id: string;
  count: number;
  offset: number;
  steps: ThinkingStep[];
}

/** GET /trace/file-diff/{step_id}/{idx} 响应 */
export interface TraceFileDiffResponse {
  step_id: string;
  file_index: number;
  type: FileOpType;
  path: string;
  diff: string;
  preview: string;
  lines_added: number;
  lines_removed: number;
  ok: boolean;
  error: string | null;
}
