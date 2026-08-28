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
  /**
   * 调用标识（根治 BUGFIX #164）。由 tool_runner / builtin dispatcher 写入，
   * 同一次调用的 tool_call 与 tool_result 事件携带相同值，前端据此配对卡片。
   * MCP 路径为 `call_<stepIndex>`，builtin 路径为 uuid4 hex。
   */
  call_id?: string;
}

/**
 * 工具结果 UI 摘要（执行过程可视化）：只给前端展示，大结果体仍只进 LLM 上下文
 * （后端 result_spill 溢出落盘）。镜像 Python `protocol/tools.py::ToolResultUi`。
 */
export interface ToolResultUi {
  summary?: string;
  icon?: string;
  path?: string;
  lines?: number;
  truncated?: boolean;
}

/**
 * 工具执行结果。
 *
 * 字段以 Python `ToolResult.to_dict()`（services/agent/src/agent/builtin/models.py）
 * 与 MCP `invoke()` 返回值为准 —— 此前本接口声明 `server` / `data` 必填，
 * 而 Python 端从来没只发 `content` / `meta`，属协议漂移（BUGFIX #164）。
 * 全部字段设为可选：两条产出路径（builtin / MCP）字段集并不完全一致。
 */
export interface ToolResult {
  /** 工具名。builtin 路径由 dispatcher 盖章，MCP 路径由 stream.py 按 call 回填。 */
  name?: string;
  /** 与对应 tool_call 一致的调用标识（前端配对首选键）。 */
  call_id?: string;
  ok?: boolean;
  /** 主体内容（read_file → string，list_dir → 数组）。 */
  content?: unknown;
  error?: string;
  hint?: string;
  meta?: Record<string, unknown>;
  needs_hitl?: boolean;
  risk_level?: ToolRiskLevel;
  // ---- MCP 路径附加字段 ----
  server?: string;
  data?: unknown;
  truncated?: boolean;
  rowsReturned?: number;
  /** UI 摘要（read_file 类工具：路径 + 行数；前端工具卡副标题直接用它） */
  ui?: ToolResultUi;
}