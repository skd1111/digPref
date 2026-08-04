/**
 * Tauri 事件订阅辅助工具。
 *
 * 通道名与 Rust 侧 `sse_bridge.rs` 的 `mod channel` 保持严格一致。
 * 新增通道时三端（Python stream.py / Rust sse_bridge.rs / 本文件）必须同步更新。
 */
import { listen as tauriListen, type UnlistenFn } from "@tauri-apps/api/event";

export async function listen<T>(
  event: string,
  handler: (payload: { payload: T }) => void,
): Promise<UnlistenFn> {
  return tauriListen<T>(event, handler);
}

// ---------- 命名事件通道（与 Rust 端同步）--------------------------------
export const EVT = {
  /** Agent 日志行（显示在 Xterm 终端） */
  AGENT_LOG: "agent://log",
  /** 助手文本消息（最终回答） */
  AGENT_MESSAGE: "agent://message",
  /** MCP 工具调用开始 */
  AGENT_TOOL_CALL: "agent://tool_call",
  /** MCP 工具返回结果（可能已截断） */
  AGENT_TOOL_RESULT: "agent://tool_result",
  /** 思维链跟踪步骤 */
  AGENT_TRACE: "agent://trace",
  /** HITL 审批请求（前端渲染审批卡片） */
  AGENT_APPROVAL_REQUEST: "agent://approval",
  /** 流正常结束 */
  AGENT_DONE: "agent://done",
  /** 流异常终止 */
  AGENT_ERROR: "agent://error",
  /** MCP 服务器状态变更 */
  MCP_STATUS: "mcp://status",

  // Phase 9 任务级协作引擎 —— 复用 Phase 8 WebSocket 网关（占位声明）
  // 后续接 Phase 8 Server 时与 graph/stream.py + sse_bridge.rs 三处同步
  /** 新评论推送（collab_comment_new） */
  COLLAB_COMMENT_NEW: "agent://collab_comment_new",
  /** @ 提醒推送（collab_mention） */
  COLLAB_MENTION: "agent://collab_mention",
  /** 分享到 IM 推送（collab_share） */
  COLLAB_SHARE: "agent://collab_share",

  // Phase 2D V0 Skill / MCP 生态
  AGENT_SKILL_MATCHED: "agent://skill_matched",

  // Phase 2C V0 LLM 路由
  AGENT_LLM_ROUTE_DECIDED: "agent://llm_route_decided",
  AGENT_LLM_DEGRADED: "agent://llm_degraded",
  AGENT_LLM_BUDGET_ALERT: "agent://llm_budget_alert",

  // Phase 2G V1.2 业务功能点导航 —— SSE 三处同步占位（V1.3 才有真 emit）
  // 模板照抄 Phase 9 / Phase 13 风格；Python 端 graph/stream.py + Rust 端
  // stream/sse_bridge.rs::channel 须同步注册同名 channel（V1.3 启动时落实）。
  /** YAML 热加载完成（V1.3 才真发） */
  BIZNAV_YAML_RELOADED: "agent://biznav_yaml_reloaded",
  /** 文件变更影响功能点推送（V1.3 才真发） */
  BIZNAV_FEATURE_AFFECTED: "agent://biznav_feature_affected",
  /** 后台 extraction 任务完成（V1.3 才真发） */
  BIZNAV_EXTRACTION_DONE: "agent://biznav_extraction_done",

  // Phase 4 V0 —— 本地端侧模型 SSE 三处同步（CLAUDE.md §4）
  /** 本地端侧模型就绪 */
  LOCALAI_READY: "agent://localai_ready",
  /** 本地端侧模型错误 */
  LOCALAI_ERROR: "agent://localai_error",

  // Phase 2F+ V1 —— 日志分析 SSE 三处同步（CLAUDE.md §4）
  /** 日志分析开始（根因 / 级别分类） */
  LOG_ANALYSIS_STARTED: "agent://log_analysis_started",
  /** 日志分析完成 */
  LOG_ANALYSIS_DONE: "agent://log_analysis_done",
  /** 日志分析失败 */
  LOG_ANALYSIS_ERROR: "agent://log_analysis_error",

  // Phase 12 V0/V1 —— 多智能体调度 SSE 三处同步（CLAUDE.md §4）
  /** 子 Agent 派生 */
  SUB_AGENT_SPAWN: "agent://sub_agent_spawn",
  /** 子 Agent 完成 */
  SUB_AGENT_DONE: "agent://sub_agent_done",
  /** 子 Agent 进度（V1 新增） */
  SUB_AGENT_PROGRESS: "agent://sub_agent_progress",

  // Phase 13 DSpark —— 推测解码 SSE 三处同步（CLAUDE.md §4）
  /** DSpark 加速状态（开始/结束 + 加速比 + 接受率） */
  DSPARK_ACCELERATION_STATUS: "agent://dspark_acceleration_status",

  // Phase 1B V1 —— 原生工具层 SSE 三处同步（CLAUDE.md §4）
  /** 内置工具开始执行（含 tool_name + risk_level + needs_hitl） */
  BUILTIN_TOOL_STARTED: "agent://builtin_tool_started",
  /** 内置工具执行完成（含 ok + content_size + elapsed_ms） */
  BUILTIN_TOOL_DONE: "agent://builtin_tool_done",
  /** 内置工具被 HITL 拒绝（含 reason + approval_id） */
  BUILTIN_TOOL_DENIED: "agent://builtin_tool_denied",

  // Phase 14 V0 —— 本地图像处理 SSE 三处同步（CLAUDE.md §4）
  /** 图像处理开始（含 processing_type + task_id） */
  IMAGE_PROCESSING_STARTED: "agent://image_processing_started",
  /** 图像处理完成（含 ok + elapsed_ms） */
  IMAGE_PROCESSING_DONE: "agent://image_processing_done",
  /** 图像处理失败（含 error message） */
  IMAGE_PROCESSING_ERROR: "agent://image_processing_error",

  // Phase 2B V0 —— SSH 会话 SSE 三处同步（CLAUDE.md §4）
  /** SSH 会话连接成功 */
  SSH_CONNECTED: "agent://ssh_connected",
  /** SSH 会话断开 */
  SSH_DISCONNECTED: "agent://ssh_disconnected",
  /** SSH 命令执行完成 */
  SSH_COMMAND_DONE: "agent://ssh_command_done",
  /** SSH 错误（连接失败 / 认证失败 / 命令异常） */
  SSH_ERROR: "agent://ssh_error",

  // Phase 5 V0 —— 审核专家 SSE 三处同步（CLAUDE.md §4）
  /** 审批任务创建（pending） */
  AUDIT_TASK_PENDING: "agent://audit_task_pending",
  /** 审批任务决策（approved/rejected/delegated） */
  AUDIT_TASK_DECIDED: "agent://audit_task_decided",
  /** 证据条目新增 */
  AUDIT_EVIDENCE_ADDED: "agent://audit_evidence_added",
  /** 合规检查完成 */
  AUDIT_COMPLIANCE_DONE: "agent://audit_compliance_done",

  // 文档风险合规审核 —— SSE 三处同步（CLAUDE.md §4）
  /** 文档分析任务开始 */
  DOC_REVIEW_STARTED: "agent://doc_review_started",
  /** 文档分类完成 */
  DOC_REVIEW_CLASSIFIED: "agent://doc_review_classified",
  /** 风险分析完成 */
  DOC_REVIEW_FINDINGS_READY: "agent://doc_review_findings_ready",
  /** 分析失败 */
  DOC_REVIEW_FAILED: "agent://doc_review_failed",

  // Phase 7 V0 —— 数据专家 SSE 三处同步（CLAUDE.md §4）
  /** SQL 查询结果就绪 */
  DATA_QUERY_RESULT: "agent://data_query_result",
  /** Python 沙箱执行结果 */
  DATA_PYTHON_RESULT: "agent://data_python_result",
  /** 图表推荐就绪 */
  DATA_CHART_READY: "agent://data_chart_ready",
  /** 导出完成 */
  DATA_EXPORT_DONE: "agent://data_export_done",

  // Phase 6 V1.5 —— 会话管理 SSE 三处同步（CLAUDE.md §4）
  /** CompressionRouter 选策略后实际执行压缩完成（含 before/after tokens + ratio） */
  SESSION_COMPRESSION_APPLIED: "agent://session_compression_applied",
  /** L3 情景记忆 → 语义记忆蒸馏完成（后台任务，含 distilled_count） */
  SESSION_MEMORY_CONSOLIDATED: "agent://session_memory_consolidated",

  // ---- Phase 2F+ V1.5: logviewer Tauri Events (Rust → frontend) ----
  /** 索引进度更新（bytes_scanned / file_size / pct） */
  LOGVIEWER_INDEX_PROGRESS: "logviewer://index-progress",
  /** Tail 新行到达 */
  LOGVIEWER_TAIL_LINE: "logviewer://tail-line",
  /** Tail 错误 */
  LOGVIEWER_TAIL_ERROR: "logviewer://tail-error",

  // Phase 15 V0 —— 前端实时预览引擎 SSE 三处同步（CLAUDE.md §4）
  /** HMR WebSocket 连接成功（含 session_id + status） */
  PREVIEW_HMR_CONNECTED: "agent://preview_hmr_connected",
  /** HMR 断开 / 重连中（含 session_id + status） */
  PREVIEW_HMR_DISCONNECTED: "agent://preview_hmr_disconnected",
  /** Vite 编译错误（含 session_id + error + file/line/column） */
  PREVIEW_BUILD_ERROR: "agent://preview_build_error",

  // Phase 18 —— 双框架（Coding vs Work）SSE 三处同步（CLAUDE.md §4）
  /** ModeRouter 路由结果（含 routing + overridden + declaration） */
  AGENT_MODE_ROUTED: "agent://mode_routed",
  /** Auto-Repair 循环进度（含 attempt + maxAttempts + validatorLevel） */
  AGENT_REPAIR_ATTEMPT: "agent://repair_attempt",
  /** 自动模式决策（含 reason + option + riskLevel，可跳审计详情） */
  AGENT_AUTO_DECISION: "agent://auto_decision",
} as const;
