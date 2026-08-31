/**
 * IPC bridge — typed wrappers around Tauri invoke().
 * Each function corresponds to a #[tauri::command] in src-tauri/src/commands/.
 */
import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import type {
  DSparkRuntimeConfig,
  DSparkConfigUpdateBody,
  DSparkDraftModelPathBody,
  DSparkDecisionRecord,
  SpeculativePolicy,
  ThinkingStep,
  TraceFileDiffResponse,
  TraceSessionResponse,
  DocDetail,
  DocFinding,
  DocSummary,
} from "@eaide/shared-protocol";

/**
 * Rust 侧签名为 `fn xxx(args: Value)` 的 _op 分发 command：
 * Tauri 按形参名映射 invoke 载荷，必须把参数包进 `args` 键，
 * 否则报 "missing required key args"（连 handler 都进不去）。
 */
const ARGS_WRAPPED_COMMANDS = new Set([
  "audit_decide",
  "doc_review",
  "doc_review_export_word",
]);

// ---- Token 用量（与 Python llm/usage_api.py 的 GET /llm/token-usage 对齐）----
export interface TokenUsageSnapshot {
  /** 当日日期 'YYYY-MM-DD' */
  day: string;
  /** 速率统计窗口（秒，默认 30） */
  window_seconds: number;
  /** 上传（prompt）速率 tokens/s */
  rate_upload_per_s: number;
  /** 下载（completion）速率 tokens/s */
  rate_download_per_s: number;
  /** 模型调用速率 次/s */
  rate_calls_per_s: number;
  /** 当日上传总量 */
  today_upload_tokens: number;
  /** 当日下载总量 */
  today_download_tokens: number;
  /** 当日合计 */
  today_total_tokens: number;
  /** 当日模型调用次数（跨重启保留） */
  today_call_count: number;
  /** 当日总费用（按模型管理 cost_per_1k_tokens 计，跨重启保留） */
  today_cost_total: number;
  /** 当日按模型费用明细（进程内，重启后重新累计） */
  cost_by_model: Record<string, number>;
}

export async function invoke<T = unknown>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  const payload = ARGS_WRAPPED_COMMANDS.has(cmd) ? { args: args ?? {} } : args;
  return tauriInvoke<T>(cmd, payload);
}

// ---------- Typed command surface ----------
export interface CredentialStatus {
  key: string;
  present: boolean;
}

// ---- MCP 服务器配置（设置页「MCP」面板；与 Agent /mcp-config 对齐）----
export interface McpServerSpec {
  command: string;
  args: string[];
  env: Record<string, string>;
  allowed_tools: string[];
  auto_start: boolean;
  working_dir: string | null;
}

export interface McpConfigResponse {
  path: string;
  exists: boolean;
  servers: Record<string, McpServerSpec>;
}

export interface McpTestResult {
  ok: boolean;
  tools?: { name: string; description: string }[];
  error?: string;
}

// Phase 5 V1: 审核专家工作台 类型镜像
export interface AuditTask {
  task_id: string;
  run_id: string;
  title: string;
  description: string;
  risk_level: string;
  status: string;
  pending_tool_call: Record<string, unknown>;
  requested_by: string;
  requested_at: string;
  decided_by?: string | null;
  decided_at?: string | null;
  decision_reason?: string | null;
  mfa_verified: boolean;
  evidence_count: number;
  compliance_issues: number;
  dual_required: boolean;
  first_approver?: string | null;
  second_approver?: string | null;
  first_approver_signed_at?: string | null;
  second_approver_signed_at?: string | null;
  meta: Record<string, unknown>;
}

export interface DecideRequest {
  action_type: string; // approve / reject / delegate / inquire / withdraw
  actor: string;
  reason: string;
  mfa_verified: boolean;
  totp_code?: string | null;
  use_rsa?: boolean;
}

export interface DecideResponse {
  task_id: string;
  action_id: string;
  action_type: string;
  new_status: string;
  signature_hash: string;
  rsa_signature?: string | null;
}

export interface DualRequest {
  actor: string;
  reason: string;
  mfa_verified: boolean;
  totp_code?: string | null;
  use_rsa?: boolean;
}

export interface DualResponse {
  task_id: string;
  phase: "dual_first" | "dual_second";
  action_id: string;
  first_approver?: string;
  second_approver?: string;
  new_status: string;
  signature_hash: string;
  rsa_signature?: string | null;
}

// ---- Phase 15 V0: 前端实时预览引擎 类型镜像（与 Python PreviewSession 对齐）----
export type PreviewFramework = "vue" | "react" | "svelte" | "html";
export type PreviewStatus =
  "starting" | "running" | "installing" | "stopped" | "errored";
export type PreviewDeviceMode = "desktop" | "tablet" | "mobile" | "custom";

export interface PreviewSession {
  id: string;
  project_path: string;
  entry_file: string;
  framework: PreviewFramework;
  port: number;
  url: string;
  status: PreviewStatus;
  created_at: number;
  last_active_at: number;
  pid?: number | null;
  install_progress: number;
  config_path?: string | null;
  error?: string | null;
}

export interface PreviewHmrStatusEvent {
  session_id: string;
  status: "connected" | "disconnected" | "reconnecting";
  timestamp: number;
}

export interface PreviewBuildErrorEvent {
  session_id: string;
  error: string;
  file?: string | null;
  line?: number | null;
  column?: number | null;
  timestamp: number;
}

/** Phase 2C V2.0：评分权重 5 维（必须 Σ=1.0）。与 Python routing_decisions / router_weights 表对齐。 */
export interface ScoringWeights {
  capability: number; // 默认 0.35
  cost: number; // 默认 0.25
  latency: number; // 默认 0.20
  compliance: number; // 默认 0.15
  availability: number; // 默认 0.05
}

export const ipc = {
  startChat: (prompt: string) => invoke<void>("agent_chat", { prompt }),
  /** Phase 18：带模式/自主性透传的 chat 启动（workMode/autonomy 随请求进后端） */
  startChatWithMode: (prompt: string, workMode: string, autonomy: string) =>
    invoke<string>("agent_chat", { prompt, workMode, autonomy }),
  /** Phase 18：自动模式风险确认弹窗确认后写授权审计 */
  confirmAutonomy: (sessionId: string, workMode: string) =>
    invoke<{ ok: boolean }>("agent_autonomy_confirm", {
      sessionId,
      workMode,
      consentVersion: "v1",
    }),
  /** Phase 18：工具链路径配置读写（设置页面板） */
  getToolchain: () =>
    invoke<{ paths: Record<string, string> }>("agent_toolchain_get"),
  saveToolchain: (paths: Record<string, string>) =>
    invoke<{ ok: boolean; paths: Record<string, string> }>(
      "agent_toolchain_save",
      {
        paths,
      },
    ),
  /** 工作空间路径配置读写（设置页面板；默认安装目录/workspace，可自定义） */
  getWorkspace: () =>
    invoke<{ path: string; custom: string | null; default: string }>(
      "agent_workspace_get",
    ),
  saveWorkspace: (path: string) =>
    invoke<{
      ok: boolean;
      path: string;
      custom: string | null;
      default: string;
    }>("agent_workspace_save", { path }),
  approve: (
    approvalId: string,
    decision: "approve" | "reject",
    operator?: string,
  ) => invoke<void>("agent_approval", { approvalId, decision, operator }),
  cancel: (runId: string) => invoke<void>("agent_cancel", { runId }),
  /** 会话标题摘要（2026-08-07）：后端失败返空 title，前端保留截断标题 */
  summarizeTitle: (userPrompt: string, assistantReply?: string) =>
    invoke<{ title: string }>("chat_summarize_title", {
      userPrompt,
      ...(assistantReply != null ? { assistantReply } : {}),
    }),

  /** 附加文件到对话（2026-08-14）：base64 内容 → 后端转文本/Markdown。
   *  失败时 ok=false + error；content 最多 12000 字符，truncated 标注截断。 */
  chatAttachFile: (body: { file_name: string; content_base64: string }) =>
    invoke<{
      ok: boolean;
      file_name: string;
      mode: 'text' | 'markdown';
      content: string;
      chars: number;
      truncated: boolean;
      error: string;
    }>("chat_attach_file", { body }),

  /** 会话历史压缩（2026-08-17）：旧对话 → 后端本地优先 LLM 链生成摘要。
   *  失败时 invoke 报错（后端 503），由调用方提示不阻塞对话。 */
  chatCompressHistory: (body: {
    messages: Array<{ role: string; content: string }>;
    historySummary?: string;
  }) =>
    invoke<{
      ok: boolean;
      summary: string;
      beforeTokens: number;
      afterTokens: number;
      messageCount: number;
    }>("chat_compress_history", { body }),

  /** Phase 19 V0 自进化：用户 👍/👎 反馈（👎 触发后端反思）。 */
  evolutionFeedback: (body: {
    sessionId: string;
    messageId: string;
    rating: 'up' | 'down';
    correction?: string;
  }) =>
    invoke<{ ok: boolean; reflected: boolean; task_signature: string }>(
      'evolution_feedback',
      { body },
    ),

  /** Phase 19 V0：经验库列表（设置页面板）。 */
  evolutionExperiences: () =>
    invoke<{
      ok: boolean;
      items: Array<{
        id: number;
        insight: string;
        tags: string[];
        applies_to: string;
        attribution: string;
        hit_count: number;
        score: number;
        status: 'active' | 'disabled';
        ts: string;
      }>;
    }>('evolution_experiences'),

  /** Phase 19 V0：经验启停切换（后端按当前态翻转）。 */
  evolutionExperienceToggle: (experienceId: number) =>
    invoke<{ ok: boolean; id: number; status: 'active' | 'disabled' }>(
      'evolution_experience_toggle',
      { experienceId },
    ),

  /** Phase 19 V0：删除经验（人工干预）。 */
  evolutionExperienceDelete: (experienceId: number) =>
    invoke<{ ok: boolean; id: number }>('evolution_experience_delete', { experienceId }),

  /** Phase 19 V1：技能蒸馏草稿列表（待审）。 */
  evolutionSkillDrafts: () =>
    invoke<{
      ok: boolean;
      items: Array<{
        id: number;
        slug: string;
        name: string;
        yaml_text: string;
        task_signature: string;
        status: string;
        ts: string;
      }>;
    }>('evolution_skill_drafts'),

  /** Phase 19 V1：草稿审核通过（后端写入 skills/ 并启用）。 */
  evolutionSkillDraftApprove: (draftId: number) =>
    invoke<{ ok: boolean; id: number; skill_id: string; path: string }>(
      'evolution_skill_draft_approve',
      { draftId },
    ),

  /** Phase 19 V1：草稿拒绝。 */
  evolutionSkillDraftReject: (draftId: number) =>
    invoke<{ ok: boolean; id: number; status: string }>('evolution_skill_draft_reject', {
      draftId,
    }),

  /** Phase 19 V1：进化看板统计。 */
  evolutionStats: () =>
    invoke<{
      ok: boolean;
      signals_total: number;
      user_signals: number;
      user_up: number;
      env_fail: number;
      judge_avg: number | null;
      experiences_active: number;
      drafts_pending: number;
    }>('evolution_stats'),

  /** Phase 19 V1.5：运行 Few-shot 影子优化实验（离线回放，同步返回结果）。 */
  evolutionPromptOptRun: (body: { skillId: string; taskSignature?: string }) =>
    invoke<{
      ok: boolean;
      skill_id: string;
      old_avg: number;
      new_avg: number;
      gain: number;
      significant: boolean;
      version_id: number | null;
      auto_adopted: boolean;
    }>('evolution_prompt_opt_run', { body }),

  /** Phase 19 V1.5：Prompt 版本列表（可按 skill 过滤）。 */
  evolutionPromptVersions: (skillId?: string) =>
    invoke<{
      ok: boolean;
      items: Array<{
        id: number;
        skill_id: string;
        version: number;
        few_shot: Array<{ role: string; content: string }>;
        gain: number | null;
        status: 'candidate' | 'active' | 'rolled_back';
        ts: string;
      }>;
    }>('evolution_prompt_versions', { ...(skillId != null ? { skillId } : {}) }),

  /** Phase 19 V1.5：采纳 Prompt 版本（写回技能 few-shot）。 */
  evolutionPromptVersionApply: (versionId: number) =>
    invoke<{ ok: boolean; id: number; skill_id: string; status: string }>(
      'evolution_prompt_version_apply',
      { versionId },
    ),

  /** Phase 19 V1.5：一键回滚到上一版本。 */
  evolutionPromptVersionRollback: (versionId: number) =>
    invoke<{ ok: boolean; id: number; skill_id: string; rolled_back_to: number }>(
      'evolution_prompt_version_rollback',
      { versionId },
    ),

  listAssets: () => invoke<unknown[]>("asset_list"),
  addAsset: (asset: Record<string, unknown>) =>
    invoke<Record<string, unknown>>("asset_add", { asset }),
  updateAsset: (id: string, patch: Record<string, unknown>) =>
    invoke<Record<string, unknown>>("asset_update", { id, patch }),
  removeAsset: (id: string) => invoke<void>("asset_remove", { id }),

  getSecret: (key: string) => invoke<string | null>("credential_get", { key }),
  setSecret: (key: string, value: string) =>
    invoke<void>("credential_set", { key, value }),
  deleteSecret: (key: string) => invoke<void>("credential_delete", { key }),
  listSecrets: (keys: string[]) =>
    invoke<CredentialStatus[]>("credential_list", { keys }),
  secretServiceName: () => invoke<string>("credential_service_name"),

  searchAudit: (query: string, limit?: number) =>
    invoke<unknown[]>("audit_search", { query, limit }),

  // Phase 5 V1: 审核专家工作台 —— TOTP MFA + 双人复核 + RSA 签名
  auditCreateTask: (body: {
    run_id: string;
    title: string;
    description: string;
    risk_level: string;
    pending_tool_call: Record<string, unknown>;
    requested_by: string;
    meta?: Record<string, unknown>;
  }) => invoke<AuditTask>("audit_decide", { ...body, _op: "create" }),
  auditListTasks: (status?: string, risk_level?: string, limit?: number) =>
    invoke<AuditTask[]>("audit_decide", {
      _op: "list",
      status,
      risk_level,
      limit,
    }),
  auditGetTask: (task_id: string) =>
    invoke<AuditTask>("audit_decide", { _op: "get", task_id }),
  auditAddEvidence: (
    task_id: string,
    body: {
      evidence_type: string;
      title: string;
      content: Record<string, unknown>;
      source: string;
    },
  ) =>
    invoke<{ evidence_id: string; task_id: string }>("audit_decide", {
      _op: "evidence",
      task_id,
      ...body,
    }),
  auditDecide: (task_id: string, body: DecideRequest) =>
    invoke<DecideResponse>("audit_decide", { _op: "decide", task_id, ...body }),
  auditDualFirst: (task_id: string, body: DualRequest) =>
    invoke<DualResponse>("audit_decide", {
      _op: "dual_first",
      task_id,
      ...body,
    }),
  auditDualSecond: (task_id: string, body: DualRequest) =>
    invoke<DualResponse>("audit_decide", {
      _op: "dual_second",
      task_id,
      ...body,
    }),
  auditVerifyChain: (task_id: string) =>
    invoke<{
      task_id: string;
      valid: boolean;
      action_count: number;
      rsa_signed_actions: number;
    }>("audit_decide", { _op: "verify", task_id }),
  auditGetTotp: (username: string) =>
    invoke<{ username: string; totp_code: string }>("audit_decide", {
      _op: "totp",
      username,
    }),
  auditGetPublicKey: () =>
    invoke<{ algorithm: string; public_key_pem: string }>("audit_decide", {
      _op: "public_key",
    }),
  auditStats: () =>
    invoke<Record<string, unknown>>("audit_decide", { _op: "stats" }),

  // 文档风险合规审核（审核专家 · 文档审核）
  docReviewRegister: (file_path: string) =>
    invoke<{ doc_id: string; file_name: string; page_count: number }>(
      "doc_review",
      {
        _op: "register",
        file_path,
      },
    ),
  docReviewList: () => invoke<DocSummary[]>("doc_review", { _op: "list" }),
  docReviewGet: (doc_id: string) =>
    invoke<DocDetail>("doc_review", { _op: "get", doc_id }),
  docReviewAnalyze: (doc_id: string) =>
    invoke<{ run_id: string; status: string }>("doc_review", {
      _op: "analyze",
      doc_id,
    }),
  docReviewFindings: (doc_id: string, run_id?: string) =>
    invoke<{
      doc_id: string;
      run_id: string;
      count: number;
      findings: DocFinding[];
    }>("doc_review", { _op: "findings", doc_id, run_id }),
  docReviewStatus: (doc_id: string) =>
    invoke<{
      doc_id: string;
      run_id?: string;
      status: string;
      error?: string;
      /** 分析进度 0..1（排队/历史无记录时为 null） */
      progress?: number | null;
    }>("doc_review", {
      _op: "status",
      doc_id,
    }),
  docReviewDelete: (doc_id: string) =>
    invoke<{ doc_id: string; deleted: boolean }>("doc_review", {
      _op: "delete",
      doc_id,
    }),

  /** 导出审核结果为 Word：Rust 下载 docx 二进制并写入 save_path */
  docReviewExportWord: (doc_id: string, mode: "full" | "risks_only", save_path: string) =>
    invoke<{ path: string; bytes: number }>("doc_review_export_word", {
      doc_id,
      mode,
      save_path,
    }),

  // Phase 2F V0: 代码导航
  codeNavJump: (body: {
    symbol: string;
    current_file: string;
    context?: string;
    line?: number;
  }) =>
    invoke<{
      file_path: string;
      line: number;
      confidence: number;
      source: "local_index" | "not_found";
      note: string | null;
    }>("code_nav_jump", { body }),

  /** 语法错误检查（2026-08-19）：编辑器当前内容 → tree-sitter 解析 → 诊断列表。
   *  file_path 只用于按后缀选语法；行列 1-based（与 Monaco marker 口径一致）。 */
  codeNavCheck: (body: { file_path: string; content: string }) =>
    invoke<{
      ok: boolean;
      supported: boolean;
      language: string;
      diagnostics: Array<{
        line: number;
        column: number;
        end_line: number;
        end_column: number;
        message: string;
      }>;
    }>("code_nav_check", { body }),

  codeNavIndex: (opts?: {
    rootPaths?: string[];
    addRoots?: string[];
    files?: string[];
  }) =>
    invoke<{
      total_files: number;
      total_symbols: number;
      last_full_scan: number | null;
      last_incremental: number | null;
      is_scanning: boolean;
    }>("code_nav_index", {
      body: {
        root_paths: opts?.rootPaths ?? null,
        add_roots: opts?.addRoots ?? null,
        files: opts?.files ?? null,
      },
    }),

  codeNavStatus: () =>
    invoke<{
      total_files: number;
      total_symbols: number;
      last_full_scan: number | null;
      last_incremental: number | null;
      is_scanning: boolean;
    }>("code_nav_status"),

  codeNavListSymbols: (name: string, kind?: string, limit?: number) =>
    invoke<
      Array<{
        name: string;
        kind: string;
        file_path: string;
        start_line: number;
        end_line: number;
        signature: string | null;
        parent_class: string | null;
        language: string;
      }>
    >("code_nav_list_symbols", {
      name,
      kind: kind ?? null,
      limit: limit ?? 10,
    }),

  codeNavExplain: (body: {
    symbol: string;
    current_file: string;
    line?: number;
    context?: string;
    /** V1 Phase 12：用户在编辑器选中的范围（后端改写 system prompt） */
    selection_start_line?: number | null;
    selection_end_line?: number | null;
    selection_text?: string | null;
  }) =>
    invoke<{
      symbol: string;
      text: string;
      source: "llm" | "mock";
      confidence: number;
      backend?: string | null;
    }>("code_nav_explain", { body }),

  /** 流式解释：增量经 EVT.CODENAV_EXPLAIN_DELTA 事件推送，invoke 返回最终结果 */
  codeNavExplainStream: (body: {
    symbol: string;
    current_file: string;
    line?: number;
    context?: string;
    selection_start_line?: number | null;
    selection_end_line?: number | null;
    selection_text?: string | null;
  }) =>
    invoke<{
      symbol: string;
      text: string;
      source: "llm" | "mock";
      confidence: number;
      backend?: string | null;
    }>("code_nav_explain_stream", { body }),

  codeNavLlmConfig: () =>
    invoke<{
      configured: boolean;
      base_url: string | null;
      model: string | null;
      has_api_key: boolean;
      timeout_s: number;
    }>("code_nav_llm_config"),

  codeNavLlmConfigReload: () =>
    invoke<{
      configured: boolean;
      base_url: string | null;
      model: string | null;
      has_api_key: boolean;
      timeout_s: number;
    }>("code_nav_llm_config_reload"),

  codeNavAllowedRoots: () =>
    invoke<{ roots: string[]; extra_env: string }>("code_nav_allowed_roots"),

  codeNavLlmBackend: () =>
    invoke<{
      bound: string | null;
      resolved: {
        name: string;
        type: string;
        base_url: string;
        model: string;
        has_api_key: boolean;
        source: "router_db_bound" | "router_db_default" | "env";
      } | null;
      candidates: Array<{
        name: string;
        type: string;
        base_url: string;
        model: string;
        enabled: boolean;
      }>;
    }>("code_nav_llm_backend"),

  codeNavLlmBackendBind: (backendName: string | null) =>
    invoke<{
      bound: string | null;
      resolved: {
        name: string;
        type: string;
        base_url: string;
        model: string;
        has_api_key: boolean;
        source: "router_db_bound" | "router_db_default" | "env";
      } | null;
      candidates: Array<{
        name: string;
        type: string;
        base_url: string;
        model: string;
        enabled: boolean;
      }>;
    }>("code_nav_llm_backend_bind", { backendName: backendName ?? null }),

  // Phase 2F V3 路径护栏
  codeNavOpenedProjects: () =>
    invoke<{ opened_projects: string[] }>("code_nav_opened_projects"),

  codeNavSyncOpenedProjects: (folders: string[]) =>
    invoke<{ opened_projects: string[] }>("code_nav_sync_opened_projects", {
      folders,
    }),

  codeNavAddOpenedProject: (folder: string) =>
    invoke<{ opened_projects: string[] }>("code_nav_add_opened_project", {
      folder,
    }),

  codeNavRemoveOpenedProject: (folder: string) =>
    invoke<{ opened_projects: string[] }>("code_nav_remove_opened_project", {
      folder,
    }),

  // Phase 2C V0：路由 / 后端管理
  routerTestConnection: (payload: {
    type: "local" | "private" | "cloud";
    base_url: string;
    model: string;
    api_key?: string;
    timeout_s?: number;
  }) =>
    invoke<{
      ok: boolean;
      latency_ms?: number;
      models?: string[];
      actual_model?: string;
      info?: string;
      error?: string;
    }>("router_test_connection", { payload }),

  /** 列出 router.db 里所有后端（持久化真源）。 */
  routerListBackends: () =>
    invoke<{
      backends: Array<{
        name: string;
        type: string;
        base_url: string;
        model_name: string;
        api_key_ref?: string | null;
        capabilities?: string[];
        max_context?: number;
        cost_per_1k_tokens?: number;
        timeout_seconds?: number;
        data_residency?: string;
        enabled: boolean;
        role?: string;
      }>;
    }>("router_list_backends"),

  /** 新建/更新后端（按 name 自动选 POST / PUT）。 */
  routerUpsertBackend: (backend: Record<string, unknown>) =>
    invoke<{
      ok: boolean;
      backend?: Record<string, unknown>;
      /** 因同类型互斥被自动停用的后端名列表 */
      disabled?: string[];
    }>("router_upsert_backend", { backend }),

  /** 删除后端。 */
  routerDeleteBackend: (name: string) =>
    invoke<{ ok: boolean }>("router_delete_backend", { name }),

  /** 保存/启停模型后热重载 LMRouter（端侧自定义 URL/端口无需重启 Agent 生效）。 */
  routerReloadContext: () =>
    invoke<{
      ok: boolean;
      ollama_max_ctx: number | null;
      private_max_ctx: number | null;
    }>("router_reload_context"),

  /** 生成限制（两级回退）：读全局默认（最大输出长度 / 默认上下文长度）。 */
  routerGetGenLimits: () =>
    invoke<{
      ok: boolean;
      limits: { max_output_tokens: number; default_context_window: number };
    }>("router_get_gen_limits"),

  /** 生成限制（两级回退）：稀疏 patch 写入 + 后端热生效。 */
  routerSetGenLimits: (limits: {
    max_output_tokens?: number;
    default_context_window?: number;
  }) =>
    invoke<{
      ok: boolean;
      limits: { max_output_tokens: number; default_context_window: number };
    }>("router_set_gen_limits", { limits }),

  /** Phase 2C V2.0：实时 metrics（circuits + budget + backends）。5 秒轮询用。 */
  routerGetMetrics: () =>
    invoke<{
      circuits: Record<string, "closed" | "open" | "half_open">;
      budget: { daily_spent: number; daily_limit: number };
      backends: Array<{
        name: string;
        type: string;
        role: string;
        enabled: boolean;
      }>;
    }>("router_get_metrics"),

  /** Phase 2C V2.0：最近 routing_decisions（router.db 真实表读取）。 */
  routerGetDecisions: (limit?: number) =>
    invoke<{
      decisions: Array<{
        request_id: string;
        user_id: string;
        task_category: string;
        primary_backend: string;
        actual_backend: string;
        fallback_used: boolean;
        cache_hit: boolean;
        latency_ms: number;
        estimated_cost: number;
        actual_cost: number;
        trace: Record<string, unknown>;
        created_at: number;
      }>;
    }>("router_get_decisions", { limit: limit ?? 50 }),

  /** Phase 2C V2.0：PUT 评分权重（落库 + 热生效 Engine）。5 维必须 Σ=1。 */
  routerSetWeights: (weights: ScoringWeights) =>
    invoke<{ ok: boolean; weights: ScoringWeights }>("router_set_weights", {
      weights,
    }),

  /** Phase 2C V2.0：读当前权重（用于 ScoringWeightsEditor 初始值）。 */
  routerGetWeights: () =>
    invoke<{ weights: ScoringWeights }>("router_get_weights"),

  /** Phase 2C V2.0：手动重置熔断器（Open → Closed）。 */
  routerResetBreaker: (backendName: string) =>
    invoke<{ ok: boolean; state: string }>("router_reset_breaker", {
      backendName,
    }),

  /** Phase 2C V2.0：Spark 模式 toggle（前端 RouterDashboard 直连）。 */
  routerSetSparkMode: (enabled: boolean) =>
    invoke<{ ok: boolean; spark_enabled: boolean }>("router_set_spark_mode", {
      enabled,
    }),

  // ---- Phase 2D: Skill/MCP 生态 ----

  /** Phase 2D：列出所有 skill。 */
  skillsList: () =>
    invoke<{ skills: Array<Record<string, unknown>> }>("skills_list"),

  /** Phase 2D：获取单个 skill。 */
  skillsGet: (skillId: string) =>
    invoke<Record<string, unknown>>("skills_get", { skillId }),

  /** Phase 2D：保存 skill（写入 YAML + 重载）。 */
  skillsSave: (skillId: string, body: Record<string, unknown>) =>
    invoke<{ ok: boolean; path: string; skill: Record<string, unknown> }>(
      "skills_save",
      { skillId, body },
    ),

  /** Phase 2D：删除 skill。 */
  skillsDelete: (skillId: string) =>
    invoke<{ ok: boolean }>("skills_delete", { skillId }),

  /** Phase 2D：导入单个 skill（已校验 JSON 或 YAML 体）。 */
  skillsImport: (body: Record<string, unknown>) =>
    invoke<{ ok: boolean; skill_id: string }>("skills_import", { body }),

  /** Phase 2D：导出全部 skills（YAML 字典）。 */
  skillsExportAll: () =>
    invoke<{ skills: Record<string, string> }>("skills_export_all"),

  /** Phase 2D：重新扫描整个 skills 目录。 */
  skillsReload: () => invoke<{ ok: boolean; count: number }>("skills_reload"),

  /** 专家团：列出全部。 */
  expertTeamsList: () => invoke<{ teams: unknown[] }>("expert_teams_list"),

  /** 专家团：获取单个。 */
  expertTeamsGet: (teamId: string) =>
    invoke<Record<string, unknown>>("expert_teams_get", { teamId }),

  /** 专家团：保存（upsert，写 YAML + 重载）。 */
  expertTeamsSave: (teamId: string, body: Record<string, unknown>) =>
    invoke<{ ok: boolean }>("expert_teams_save", { teamId, body }),

  /** 专家团：删除。 */
  expertTeamsDelete: (teamId: string) =>
    invoke<{ ok: boolean }>("expert_teams_delete", { teamId }),

  /** 专家团：导入（完整对象 或 {content: "...YAML 文本"}，后端解析）。 */
  expertTeamsImport: (body: Record<string, unknown>) =>
    invoke<{ ok: boolean; team_id: string }>("expert_teams_import", { body }),

  /** 专家团：导出全部（id → YAML 文本）。 */
  expertTeamsExportAll: () =>
    invoke<{ teams: Record<string, string> }>("expert_teams_export_all"),

  /** 专家团：业务 → 专家团推荐（预设→LLM 三级降级→关键词，永不报错）。 */
  expertTeamsRecommend: (body: Record<string, unknown>) =>
    invoke<{
      team_ids: string[];
      confidence: number;
      reasoning: string;
      source: string;
    }>("expert_teams_recommend", { body }),

  /** 专家团：导入资产包 zip（team.yaml 提示词 + templates/ 交付物模板，base64）。 */
  expertTeamsImportPackage: (fileName: string, contentBase64: string) =>
    invoke<{ ok: boolean; team_id: string; templates: string[] }>("expert_teams_import_package", {
      fileName,
      contentBase64,
    }),

  /** 专家团：导出资产包 zip（base64，含 team.yaml + 当前生效模板）。 */
  expertTeamsExportPackage: (teamId: string) =>
    invoke<{ file_name: string; content_base64: string }>("expert_teams_export_package", {
      teamId,
    }),

  /**
   * 阻塞等 Agent 就绪（轮询 GET /health）。返回 ready=true 时方可放心发业务请求。
   * timeout_s 默认 30s。失败时 ready=false + error 信息。
   */
  agentWaitReady: (timeoutS?: number) =>
    invoke<{
      ready: boolean;
      elapsed_ms?: number;
      health_url?: string;
      error?: string;
    }>("agent_wait_ready", { timeoutS }),

  /**
   * GET /llm/token-usage —— Token 用量快照（状态栏「Agent: 就绪」旁实时展示）。
   * 速率区分上传（prompt）/ 下载（completion），当日总量跨重启保留。
   */
  tokenUsageGet: () => invoke<TokenUsageSnapshot>("token_usage_get"),

  /** GET /version — Agent 启动指纹（pid + boot_time + endpoints 列表）。用于诊断 404。 */
  agentGetVersion: () =>
    invoke<{
      ok: boolean;
      status?: number;
      version?: {
        service: string;
        pid: number;
        boot_time: number;
        uptime_s: number;
        endpoints: string[];
      };
      error?: string;
    }>("agent_get_version"),

  /** 手动重启 Agent：杀掉 :8765 占用者 + 等下次 spawn。 */
  agentRestartNow: () =>
    invoke<{ ok: boolean; port_freed?: boolean }>("agent_restart_now"),

  /** 读 eaide.log 末尾 60 行（最后一次诊断）。 */
  agentReadLog: (lines?: number) =>
    invoke<{
      ok: boolean;
      path: string;
      tail: string;
      line_count: number;
      hint?: string;
    }>("agent_read_log", { lines }),

  // Phase 12 V0：多智能体 Orchestrator
  // Phase 12 V2：已移除 orchestrator_spawn —— 是否启用多智能体由 Agent
  // 编排决策器自动判断（LLM 决策 + 安全门槛），不向用户暴露手动派生入口。

  orchestratorList: () =>
    invoke<{
      total_nodes: number;
      items: Array<{
        sub_agent_id: string;
        parent_run_id: string;
        parent_sub_agent_id: string | null;
        status: string;
        task_type: string | null;
        started_at: string | null;
        finished_at: string | null;
        latency_ms: number;
        confidence: number;
      }>;
    }>("orchestrator_list"),

  orchestratorGet: (subAgentId: string) =>
    invoke<Record<string, unknown>>("orchestrator_get", { subAgentId }),

  orchestratorTreeStats: () =>
    invoke<{
      total_nodes: number;
      max_depth: number;
      max_total_nodes: number;
      headroom_depth: number;
      headroom_nodes: number;
    }>("orchestrator_tree_stats"),

  orchestratorCancel: (subAgentId: string) =>
    invoke<{ ok: boolean; sub_agent_id: string }>("orchestrator_cancel", {
      subAgentId,
    }),

  // Phase 12 V1.5：异步派发 + DLQ + 评测 + 回放（9 个新 wrapper）
  orchestratorDispatch: (
    spec: Record<string, unknown>,
    priority?: "high" | "normal" | "low",
  ) =>
    invoke<{ task_id: string; status: string; priority: string }>(
      "orchestrator_dispatch",
      {
        spec,
        priority,
      },
    ),

  orchestratorRunUntilDrained: (timeout?: number) =>
    invoke<{ completed: number; items: unknown[] }>(
      "orchestrator_run_until_drained",
      { timeout },
    ),

  orchestratorCancelAll: () =>
    invoke<{ ok: boolean; elapsed_ms: number }>("orchestrator_cancel_all"),

  orchestratorDlqList: (
    state?: "open" | "closed" | "requeued",
    limit?: number,
  ) =>
    invoke<{
      state: string;
      count: number;
      items: Array<Record<string, unknown>>;
    }>("orchestrator_dlq_list", { state, limit }),

  orchestratorDlqRequeue: (taskId: string, note?: string) =>
    invoke<{ ok: boolean; task_id: string; state: string }>(
      "orchestrator_dlq_requeue",
      {
        taskId,
        note,
      },
    ),

  orchestratorDlqClose: (taskId: string, note?: string) =>
    invoke<{ ok: boolean; task_id: string; state: string }>(
      "orchestrator_dlq_close",
      {
        taskId,
        note,
      },
    ),

  orchestratorMetrics: () =>
    invoke<Record<string, unknown>>("orchestrator_metrics"),

  orchestratorQueueStats: () =>
    invoke<{
      closed: boolean;
      pending: number;
      by_priority: { high: number; normal: number; low: number };
      enqueued_total: number;
      dequeued_total: number;
      dedup_hits: number;
      backlog_alert: boolean;
    }>("orchestrator_queue_stats"),

  orchestratorReplay: (correlationId: string, limit?: number) =>
    invoke<{
      correlation_id: string;
      summary: Record<string, unknown>;
      event_count: number;
      events: Array<Record<string, unknown>>;
      tree: Array<Record<string, unknown>>;
    }>("orchestrator_replay", { correlationId, limit }),

  // Phase 13 V0：DSpark 推测解码（类型来自 @eaide/shared-protocol）
  dsparkGetConfig: () => invoke<DSparkRuntimeConfig>("dspark_get_config"),

  dsparkGetPolicies: () => invoke<SpeculativePolicy[]>("dspark_get_policies"),

  dsparkGetRecent: (limit?: number) =>
    invoke<DSparkDecisionRecord[]>("dspark_get_recent", { limit: limit ?? 20 }),

  dsparkReloadPolicies: () =>
    invoke<{ ok: boolean; policy_count: number }>("dspark_reload_policies"),

  /** 设置草稿模型路径（持久化到 dspark.json + 运行时立即生效）。空串 = 禁用 */
  dsparkSetDraftModelPath: (path: string | null) =>
    invoke<{
      ok: boolean;
      draft_model_path: string | null;
      persisted_to: string;
    }>("dspark_set_draft_model_path", {
      path: path,
    } satisfies DSparkDraftModelPathBody),

  /** 更新 DSpark 全量配置（POST /dspark/config）。任意字段可选 */
  dsparkUpdateConfig: (body: DSparkConfigUpdateBody) =>
    invoke<{
      ok: boolean;
      config: DSparkRuntimeConfig;
    }>("dspark_update_config", { body }),

  // Phase 2G V1.2：业务功能点导航 8 wrapper（V1.1 backend 已 green / 9 Tauri cmd 见
  // apps/desktop/src-tauri/src/commands/biznav.rs）。返回类型故意宽松用 unknown[] /
  // Record<string, unknown> —— 真接 V1.5 才会收窄到 typed Feature[] / Feature。
  biznavExtract: (body: { project_name: string; project_root: string }) =>
    invoke<{ job_id: number; status: string }>("biznav_extract", { body }),

  biznavStatus: (project_name: string) =>
    invoke<{
      project_name: string;
      has_job: boolean;
      job?: Record<string, unknown>;
    }>("biznav_status", { projectName: project_name }),

  biznavListFeatures: (opts?: {
    project_name?: string;
    category?: string;
    include_deleted?: boolean;
  }) => invoke<unknown[]>("biznav_list_features", opts ?? {}),

  biznavGetFeature: (feature_id: string, project_name: string) =>
    invoke<Record<string, unknown> | null>("biznav_get_feature", {
      feature_id,
      project_name,
    }),

  biznavUpsertFeature: (
    feature_id: string,
    project_name: string,
    body: Record<string, unknown>,
  ) =>
    invoke<{ ok: boolean; version: number }>("biznav_upsert_feature", {
      feature_id,
      project_name,
      body,
    }),

  biznavDeleteFeature: (feature_id: string, project_name: string) =>
    invoke<{ ok: boolean }>("biznav_delete_feature", {
      feature_id,
      project_name,
    }),

  biznavImportYaml: (body: {
    project_name: string;
    yaml_text: string;
    merge?: boolean;
  }) =>
    invoke<{
      imported: number;
      conflicts: number;
    }>("biznav_import_yaml", { body }),

  biznavExportYaml: (project_name: string) =>
    invoke<{
      yaml_text: string;
      feature_count: number;
    }>("biznav_export_yaml", { project_name }),

  biznavAffected: (file_path: string, project_name: string) =>
    invoke<unknown[]>("biznav_affected", {
      file_path,
      project_name,
    }),

  /** 项目画像（init 风格，2026-08-05）：导入工程时生成，chat 发送时前置注入提示词 */
  biznavProfile: (project_name: string) =>
    invoke<{
      project_name: string;
      has_profile: boolean;
      profile: string;
      project_root?: string;
      updated_at?: number;
    }>("biznav_profile", { projectName: project_name }),

  // reqflow V1：运营专家需求改造工作流（需求卡片）10 wrapper，
  // Tauri cmd 见 apps/desktop/src-tauri/src/commands/reqflow.rs。
  reqflowCreateBatch: (body: {
    project_name: string;
    name?: string;
    created_by?: string;
  }) => invoke<Record<string, unknown>>("reqflow_create_batch", { body }),

  reqflowListBatches: (projectName?: string) =>
    invoke<{
      batches: Record<string, unknown>[];
      stats: Record<string, Record<string, number>>;
    }>("reqflow_list_batches", { projectName }),

  reqflowGenerateCard: (body: {
    feature_ids: string[];
    project_name: string;
    system_name?: string;
    conversation_summary: string;
    session_id?: string;
  }) => invoke<{ draft: Record<string, unknown> }>("reqflow_generate_card", { body }),

  reqflowListCards: (opts?: {
    batchId?: string;
    status?: string;
    featureId?: string;
    projectName?: string;
  }) =>
    invoke<{ cards: Record<string, unknown>[]; total: number }>(
      "reqflow_list_cards",
      {
        batchId: opts?.batchId,
        status: opts?.status,
        featureId: opts?.featureId,
        projectName: opts?.projectName,
      },
    ),

  reqflowCreateCard: (body: Record<string, unknown>) =>
    invoke<Record<string, unknown>>("reqflow_create_card", { body }),

  reqflowUpdateCard: (cardId: string, body: Record<string, unknown>) =>
    invoke<Record<string, unknown>>("reqflow_update_card", { cardId, body }),

  reqflowDeleteCard: (cardId: string) =>
    invoke<{ ok: boolean }>("reqflow_delete_card", { cardId }),

  reqflowListCardVersions: (cardId: string) =>
    invoke<{
      card_id: string;
      current_version: number;
      versions: { version: number; changed_by: string; created_at: number }[];
    }>("reqflow_list_card_versions", { cardId }),

  reqflowGetCardVersion: (cardId: string, version: number) =>
    invoke<{
      card_id: string;
      version: number;
      snapshot: Record<string, unknown>;
    }>("reqflow_get_card_version", { cardId, version }),

  /** md → {markdown}；docx → {base64, filename}（前端 atob 还原二进制落盘） */
  reqflowExport: (batchId: string, format: 'md' | 'docx') =>
    invoke<{
      markdown?: string;
      base64?: string;
      filename?: string;
      format?: string;
    }>("reqflow_export", { batchId, format }),

  /** 导出文件落盘（md 传 content_text，docx 传 content_base64） */
  reqflowWriteExport: (
    path: string,
    payload: { content_text?: string; content_base64?: string },
  ) =>
    invoke<{ ok: boolean; path: string; bytes: number }>("reqflow_write_export", {
      path,
      contentText: payload.content_text,
      contentBase64: payload.content_base64,
    }),

  // Phase 2H：运营工作台业务记录（记录卡片 CRUD + AI 总结）
  opsCreateRecord: (body: Record<string, unknown>) =>
    invoke<Record<string, unknown>>("ops_create_record", { body }),

  opsListRecords: (opts?: {
    featureId?: string;
    projectName?: string;
    limit?: number;
  }) =>
    invoke<{ records: Record<string, unknown>[]; total: number }>(
      "ops_list_records",
      {
        featureId: opts?.featureId,
        projectName: opts?.projectName,
        limit: opts?.limit,
      },
    ),

  opsGetRecord: (recordId: string) =>
    invoke<Record<string, unknown>>("ops_get_record", { recordId }),

  opsDeleteRecord: (recordId: string) =>
    invoke<{ ok: boolean }>("ops_delete_record", { recordId }),

  opsSummarizeRecord: (body: {
    feature_id: string;
    project_name: string;
    business_type?: string;
    conversation: Array<{ role: string; content: string }>;
    session_id?: string;
  }) =>
    invoke<{ draft: Record<string, unknown> }>("ops_summarize_record", { body }),

  // 专家验收工作流 Case（2026-08-10，取代运营模式大 Chat）
  opsCaseGet: (opts?: { projectName?: string; featureId?: string }) =>
    invoke<{
      case_id: string;
      files: Record<string, unknown>[];
      qa: Record<string, unknown>[];
      drafts?: Record<string, unknown>[];
    }>(
      "ops_case_get",
      { projectName: opts?.projectName, featureId: opts?.featureId },
    ),

  /** 清空 Case 重新开始办理（BUGFIX #85）。 */
  opsCaseClear: (opts?: { projectName?: string; featureId?: string }) =>
    invoke<{ ok: boolean; case_id: string; files: number; qa: number; drafts: number }>(
      "ops_case_clear",
      { projectName: opts?.projectName, featureId: opts?.featureId },
    ),

  opsCaseFileAdd: (body: {
    case_id: string;
    team_id: string;
    member_key: string;
    file_name: string;
    content_base64: string;
  }) => invoke<Record<string, unknown>>("ops_case_file_add", { body }),

  opsCaseFileReview: (fileId: string) =>
    invoke<Record<string, unknown>>("ops_case_file_review", { fileId }),

  opsCaseFileOverride: (fileId: string, body: { status: string; note?: string }) =>
    invoke<Record<string, unknown>>("ops_case_file_override", { fileId, body }),

  opsCaseFileDelete: (fileId: string) =>
    invoke<{ ok: boolean }>("ops_case_file_delete", { fileId }),

  /** 交付物柜：预览材料内容（BUGFIX #79，base64）。 */
  opsCaseFileContent: (fileId: string) =>
    invoke<{ file_name: string; content_base64: string }>("ops_case_file_content", { fileId }),

  /** 交付物柜：另存到指定路径（后端直接复制文件）。 */
  opsCaseFileSaveAs: (fileId: string, targetPath: string) =>
    invoke<{ ok: boolean; path: string }>(
      "ops_case_file_save_as",
      { fileId, body: { target_path: targetPath } },
    ),

  opsCaseAsk: (body: {
    case_id: string;
    team_id: string;
    member_key: string;
    question: string;
  }) =>
    invoke<{ qa: Record<string, unknown>; draft?: Record<string, unknown> }>(
      "ops_case_ask",
      { body },
    ),

  /** 交付草稿：保存填写值（BUGFIX #78 界面直填）。 */
  opsCaseDraftSave: (draftId: string, body: { values: Record<string, string> }) =>
    invoke<Record<string, unknown>>("ops_case_draft_save", { draftId, body }),

  /** 交付草稿：点交付物直开表单（零 LLM，模板来自专家团 yaml；幂等复用未通过草稿）。 */
  opsCaseDraftDirect: (body: {
    case_id: string;
    team_id: string;
    member_key: string;
    output_name: string;
  }) =>
    invoke<{ draft: Record<string, unknown>; reused: boolean }>("ops_case_draft_direct", {
      body,
    }),

  /** 交付草稿：提交 → 自动入材料走专家审核，通过即入交付物。 */
  opsCaseDraftSubmit: (draftId: string) =>
    invoke<{ draft: Record<string, unknown>; file: Record<string, unknown> }>(
      "ops_case_draft_submit",
      { draftId },
    ),

  opsCaseExport: (body: {
    case_id: string;
    target_path: string;
    feature_name?: string;
    team_id?: string;
    team_name?: string;
    checklist?: string[];
  }) =>
    invoke<{ ok: boolean; path: string; file_count: number }>("ops_case_export", { body }),

  opsCaseCrosscheck: (caseId: string) =>
    invoke<{
      case_id: string;
      inconsistencies: Array<{ field: string; values: Array<{ file: string; value: string }> }>;
      low_confidence: Array<{ file: string; field: string; value: string; confidence: number }>;
      consistent: boolean;
    }>("ops_case_crosscheck", { caseId }),

  // Phase 2H：数据字典（公共参数独立维护，Skill 按 key 引用）
  dictListItems: (category?: string) =>
    invoke<{ items: Record<string, unknown>[]; total: number }>(
      "dict_list_items",
      { category },
    ),

  dictSearchItems: (q: string, limit?: number) =>
    invoke<{ query: string; items: Record<string, unknown>[]; total: number }>(
      "dict_search_items",
      { q, limit },
    ),

  dictListCategories: () =>
    invoke<{ categories: string[] }>("dict_list_categories"),

  dictCreateItem: (body: {
    key: string;
    category: string;
    label: string;
    value: string;
    description?: string;
    updated_by?: string;
  }) => invoke<Record<string, unknown>>("dict_create_item", { body }),

  dictUpdateItem: (key: string, body: Record<string, unknown>) =>
    invoke<Record<string, unknown>>("dict_update_item", { key, body }),

  dictDeleteItem: (key: string) =>
    invoke<{ ok: boolean }>("dict_delete_item", { key }),

  // 在系统资源管理器中定位到文件（Tab 右键菜单）
  revealInExplorer: (path: string) =>
    invoke<string>("reveal_in_explorer", { path }),

  // 用系统默认程序打开文件（2026-08-26：对话中文件路径点击直接打开）
  openWithDefault: (path: string) =>
    invoke<string>("open_with_default", { path }),

  // 任务目录文件清单（2026-08-26）：产物/中间文件，验收清理卡用
  taskFilesGet: (taskId: string) =>
    invoke<{ task_dir: string; task_dir_exists: boolean; artifacts: string[]; intermediates: string[] }>(
      "task_files_get",
      { taskId },
    ),

  // 验收后清理任务目录内除产物外的文件（2026-08-26）
  taskCleanup: (taskId: string, keep: string[] = []) =>
    invoke<{ ok: boolean; deleted: string[]; kept: string[]; task_dir_removed: boolean }>(
      "task_cleanup",
      { taskId, keep },
    ),

  // 开发者工具开关（F12 / Ctrl+Shift+I；受 config.yaml 的 devtools 控制）
  openDevtools: () => invoke<string>("open_devtools"),

  // Phase 2F V3：File → Open File 读文本（前端自定义 command，避免装 fs 插件）
  readTextFile: (path: string) => invoke<string>("read_text_file", { path }),

  // File → Open Folder：列出目录子条目（文件树渲染）
  listDirEntries: (path: string) =>
    invoke<Array<{ name: string; path: string; is_dir: boolean }>>(
      "list_dir_entries",
      { path },
    ),

  // 文件树右键编译（2026-08-19）：文件/目录多选 → 按扩展名分组调 javac/py_compile/gcc
  // outputDir 留空 → Rust 兜底 安装目录/workspace/compiled；前端一般先解析 workspace 传入
  compileFiles: (
    items: Array<{ path: string; is_dir: boolean }>,
    outputDir: string,
  ) =>
    invoke<{
      output_dir: string;
      total: number;
      ok_count: number;
      failed_count: number;
      truncated: boolean;
      entries: Array<{ path: string; ok: boolean; message: string }>;
      commands: string[];
    }>("compile_files", { items, outputDir }),

  // 编译配置读写（设置页「编译配置」面板；编译器目录手动选择，留空自动探测 PATH）
  compileConfigGet: () =>
    invoke<{
      javac_dir: string;
      python_dir: string;
      gcc_dir: string;
      output_dir: string;
    }>("compile_config_get"),
  compileConfigSave: (config: {
    javac_dir: string;
    python_dir: string;
    gcc_dir: string;
    output_dir: string;
  }) => invoke<typeof config>("compile_config_save", { config }),

  // Phase 6 V0：会话管理（外部 KB 调用接口保留 Phase 4 / 第三方接入点）
  sessionsCreate: (body: {
    title: string;
    owner?: string;
    project_name?: string;
    metadata?: Record<string, unknown>;
  }) =>
    invoke<{
      id: string;
      title: string;
      owner: string;
      project_name: string;
      status: string;
      created_at: number;
      updated_at: number;
      thread_id: string;
      metadata: Record<string, unknown>;
      parent_session_id: string | null;
      branch_from_checkpoint_id: string | null;
      branch_label: string;
      share_tokens: Array<Record<string, unknown>>;
      permissions: Record<string, string>;
      shared_at: number;
    }>("sessions_create", { body }),

  sessionsList: (opts?: {
    status?: string;
    project_name?: string;
    limit?: number;
  }) =>
    invoke<
      Array<{
        id: string;
        title: string;
        owner: string;
        project_name: string;
        status: string;
        created_at: number;
        updated_at: number;
        thread_id: string;
        metadata: Record<string, unknown>;
        parent_session_id: string | null;
        branch_from_checkpoint_id: string | null;
        branch_label: string;
        share_tokens: Array<Record<string, unknown>>;
        permissions: Record<string, string>;
        shared_at: number;
      }>
    >("sessions_list", opts ?? {}),

  sessionsGet: (session_id: string) =>
    invoke<{
      id: string;
      title: string;
      owner: string;
      project_name: string;
      status: string;
      created_at: number;
      updated_at: number;
      thread_id: string;
      metadata: Record<string, unknown>;
      parent_session_id: string | null;
      branch_from_checkpoint_id: string | null;
      branch_label: string;
      share_tokens: Array<Record<string, unknown>>;
      permissions: Record<string, string>;
      shared_at: number;
      messages: Array<Record<string, unknown>>;
      checkpoints: Array<Record<string, unknown>>;
      // BUGFIX：Tauri command 参数需 camelCase（sessionId），传 session_id 会 missing required key
    }>("sessions_get", { sessionId: session_id }),

  sessionsDelete: (session_id: string) =>
    invoke<void>("sessions_delete", { sessionId: session_id }),

  sessionsKbSearch: (body: { query: string; top_k?: number }) =>
    invoke<{
      backend: string;
      elapsed_ms: number;
      results: Array<Record<string, unknown>>;
      snippet: string;
    }>("sessions_kb_search", { body }),

  // ---- Phase 6 V1.5：会话管理扩展 ----

  /** GET /sessions/{id}/stats —— 消息/checkpoint/事件链/压缩/分支数 快照 */
  sessionsStats: (session_id: string) =>
    invoke<{
      session_id: string;
      title: string;
      owner: string;
      status: string;
      is_branch: boolean;
      parent_session_id: string | null;
      branch_label: string;
      message_count: number;
      checkpoint_count: number;
      event_chain_count: number;
      compression_count: number;
      branch_count: number;
      created_at: number;
      updated_at: number;
    }>("sessions_stats", { sessionId: session_id }),

  /** POST /sessions/{id}/messages —— 追加消息 */
  sessionsAppendMessage: (
    session_id: string,
    body: {
      role?: string;
      content?: string;
      tool_call_id?: string | null;
      tool_name?: string | null;
      tool_args?: Record<string, unknown> | null;
      tool_result?: string | null;
      metadata?: Record<string, unknown>;
      actor?: string;
    },
  ) =>
    invoke<{ message_id: number; session_id: string; created_at: number }>(
      "sessions_append_message",
      { sessionId: session_id, body },
    ),

  /** POST /sessions/{id}/checkpoints —— 记录 LangGraph checkpoint 引用 */
  sessionsRecordCheckpoint: (
    session_id: string,
    body: {
      thread_id: string;
      checkpoint_id: string;
      label?: string;
      description?: string;
      metadata?: Record<string, unknown>;
    },
  ) =>
    invoke<{ checkpoint_id: number; session_id: string; created_at: number }>(
      "sessions_record_checkpoint",
      { sessionId: session_id, body },
    ),

  /** POST /sessions/search —— FTS5 全文搜索（标题 + 消息 + 工具） */
  sessionsSearch: (body: {
    query: string;
    project_name?: string;
    limit?: number;
  }) =>
    invoke<{
      query: string;
      total: number;
      hits: Array<{
        session_id: string;
        created_at: number;
        title: string;
        content_snippet: string;
        tool_name: string;
        tool_result: string;
        relevance: number;
      }>;
    }>("sessions_search", { body }),

  /** POST /sessions/{id}/branch —— 创建分支会话 */
  sessionsBranchCreate: (
    session_id: string,
    body: {
      branch_label: string;
      from_checkpoint_id?: string | null;
      title_suffix?: string;
      actor?: string;
    },
  ) =>
    invoke<{
      id: string;
      title: string;
      parent_session_id: string | null;
      branch_from_checkpoint_id: string | null;
      branch_label: string;
      created_at: number;
      updated_at: number;
      status: string;
    }>("sessions_branch_create", { sessionId: session_id, body }),

  /** GET /sessions/{id}/branches —— 列出分支 */
  sessionsBranchesList: (session_id: string) =>
    invoke<{
      parent_session_id: string;
      total: number;
      branches: Array<{
        id: string;
        title: string;
        parent_session_id: string | null;
        branch_from_checkpoint_id: string | null;
        branch_label: string;
        created_at: number;
        updated_at: number;
        status: string;
      }>;
    }>("sessions_branches_list", { sessionId: session_id }),

  /** POST /sessions/{id}/share —— 创建分享令牌（owner only） */
  sessionsShareCreate: (
    session_id: string,
    body: {
      permission?: "read" | "write";
      expires_in_ms?: number | null;
      actor?: string;
    },
  ) =>
    invoke<{
      token: string;
      permission: "read" | "write";
      created_at: number;
      expires_at: number | null;
    }>("sessions_share_create", { sessionId: session_id, body }),

  /** DELETE /sessions/{id}/share/{token} —— 撤销分享令牌（owner only） */
  sessionsShareRevoke: (session_id: string, token: string, actor?: string) =>
    invoke<void>("sessions_share_revoke", {
      sessionId: session_id,
      token,
      actor: actor ?? null,
    }),

  /** POST /sessions/{id}/share/grant —— 授予 actor 权限（owner only） */
  sessionsShareGrant: (
    session_id: string,
    body: {
      target_actor: string;
      permission: "read" | "write";
      granter?: string;
    },
  ) =>
    invoke<{ ok: boolean; session_id: string; granted_to: string }>(
      "sessions_share_grant",
      { sessionId: session_id, body },
    ),

  /** GET /sessions/{id}/share —— 列出 share_tokens + permissions（owner only） */
  sessionsShareList: (session_id: string, actor?: string) =>
    invoke<{
      session_id: string;
      share_tokens: Array<Record<string, unknown>>;
      permissions: Record<string, string>;
    }>("sessions_share_list", { sessionId: session_id, actor: actor ?? null }),

  /** POST /sessions/{id}/export —— 加密 .eas 导出 */
  sessionsExport: (
    session_id: string,
    body: {
      output_path: string;
      actor?: string;
      include_messages?: boolean;
      include_event_chain?: boolean;
      scrub_pii?: boolean;
    },
  ) =>
    invoke<{
      path: string;
      bytes: number;
      checksum: string;
      exported_at: number;
    }>("sessions_export", { sessionId: session_id, body }),

  /** POST /sessions/import —— 从 .eas 导入会话 */
  sessionsImport: (body: {
    eas_path: string;
    actor?: string;
    import_as_branch?: boolean;
    parent_session_id?: string | null;
  }) =>
    invoke<{
      new_session_id: string;
      message_count: number;
      checkpoint_count: number;
      event_count: number;
      checksum: string;
      chain_check: Record<string, unknown>;
    }>("sessions_import", { body }),

  /** GET /sessions/recovery —— 启动恢复扫描 */
  sessionsRecovery: (opts?: { idle_threshold_ms?: number; limit?: number }) =>
    invoke<{
      total: number;
      resumable_ids: string[];
      oldest_idle_ms: number;
      generated_at: number;
      threshold_ms: number;
      needs_recovery: boolean;
    }>("sessions_recovery", opts ?? {}),

  /** GET /sessions/{id}/event-chain —— 列出 SessionEvent 哈希链 */
  sessionsEventChain: (session_id: string, limit?: number) =>
    invoke<{
      session_id: string;
      total: number;
      entries: Array<{
        id: number;
        session_id: string;
        event_type: string;
        payload: Record<string, unknown>;
        prev_hash: string;
        hash: string;
        actor: string;
        created_at: number;
      }>;
    }>("sessions_event_chain", { sessionId: session_id, limit: limit ?? null }),

  /** POST /sessions/{id}/event-chain/verify —— 校验 SessionEvent 哈希链完整性 */
  sessionsEventChainVerify: (session_id: string) =>
    invoke<{
      session_id: string;
      valid: boolean;
      total: number;
      broken_at_id: number | null;
      broken_reason: string | null;
    }>("sessions_event_chain_verify", { sessionId: session_id }),

  // ---- Phase 4 V0 本地端侧模型 ----
  localaiStatus: () =>
    invoke<{ small: boolean; vision: boolean; embedding: boolean }>(
      "localai_status",
    ),

  localaiHealth: () =>
    invoke<{ healthy: boolean; models: Record<string, boolean> }>(
      "localai_health",
    ),

  knowledgeSearch: (body: { query: string; top_k?: number }) =>
    invoke<{
      query: string;
      results: Array<{
        doc_id: string;
        title: string;
        snippet: string;
        score: number;
        source_url: string;
      }>;
      backend: string;
      elapsed_ms: number;
    }>("knowledge_search", { body }),

  knowledgeStatus: () =>
    invoke<{ available: boolean; backend: string }>("knowledge_status"),

  // ---- Phase 7 V1：数据专家模式 ----

  /** GET /data/sources —— 数据源列表 */
  dataListSources: () =>
    invoke<
      Array<{
        id: string;
        name: string;
        type: string;
        connection_ref: string;
        schema_cache: Array<{
          name: string;
          comment: string;
          columns: Array<{ name: string; dtype: string; comment: string }>;
        }>;
        updated_at: number;
      }>
    >("data_list_sources"),

  /** POST /data/sources/{id}/sync —— 同步 Schema 元数据 */
  dataSyncSchema: (sourceId: string) =>
    invoke<{ ok: boolean; tables_synced?: number }>("data_sync_schema", {
      sourceId,
    }),

  /** POST /data/nl2sql —— 自然语言 → SQL */
  dataNl2sql: (question: string, sourceId?: string) =>
    invoke<{
      sql: string;
      tables_used: string[];
      dictionary_context: string;
      confidence: number;
    }>("data_nl2sql", { question, sourceId: sourceId ?? null }),

  /** POST /data/sql/run —— 执行只读 SQL */
  dataRunSql: (sql: string, sourceId?: string, confirmed?: boolean) =>
    invoke<{
      ok: boolean;
      task_id?: string;
      columns: string[];
      dtypes: string[];
      rows: Array<Array<string | number>>;
      result_data_ref?: string;
      stream_ref?: string;
      row_count: number;
      elapsed_ms: number;
      truncated: boolean;
      error?: string;
      /** 重查询 HITL：后端返回 needs_confirm=true 时需用户确认后 confirmed=true 重提 */
      needs_confirm?: boolean;
      message?: string;
      sql?: string;
    }>("data_run_sql", {
      sql,
      sourceId: sourceId ?? null,
      confirmed: confirmed ?? false,
    }),

  /** POST /data/python/run —— 沙箱执行 Python */
  dataRunPython: (script: string, taskId?: string) =>
    invoke<{
      ok: boolean;
      stdout: string;
      error: string;
      columns: string[];
      rows: Array<Array<string | number>>;
      row_count: number;
      mem_peak_mb: number;
      elapsed_s: number;
    }>("data_run_python", { script, taskId: taskId ?? null }),

  /** POST /data/chart/recommend —— 图表推荐 */
  dataChartRecommend: (columns: string[], dtypes: string[], rowCount: number) =>
    invoke<{
      chart_type: string;
      x_index: number;
      y_index: number;
      reason: string;
    }>("data_chart_recommend", { columns, dtypes, rowCount }),

  /** POST /data/export/{fmt} —— 导出（task_id 优先，服务端取数） */
  dataExport: (
    fmt: string,
    columns: string[],
    rows: Array<Array<string | number>>,
    title?: string,
    taskId?: string,
    outputPath?: string,
  ) =>
    invoke<{
      path: string;
      md5: string;
      row_count: number;
      watermark: string;
      format: string;
    }>("data_export", {
      fmt,
      columns,
      rows,
      title: title ?? "数据报表",
      taskId: taskId ?? null,
      // 导出路径选择（2026-08-18）：save 对话框选中的目标路径，空 = 后端默认临时目录
      outputPath: outputPath ?? "",
    }),

  /** WS 中继：拉取大结果集 Arrow 流（结果经 EVT.DATA_STREAM_CHUNK/DONE 事件送达） */
  dataStreamResult: (taskId: string) =>
    invoke<number>("data_stream_result", { taskId }),

  /** GET /data/tasks —— 历史分析任务列表 */
  dataListTasks: (limit?: number) =>
    invoke<{
      tasks: Array<{
        id: string;
        name: string;
        query_sql: string;
        result_metadata: { columns?: string[]; row_count?: number } | null;
        result_data_ref: string;
        created_at: number;
      }>;
      count: number;
    }>("data_list_tasks", { limit: limit ?? null }),

  /** POST /data/templates —— 保存报表模板 */
  dataSaveTemplate: (
    name: string,
    description?: string,
    taskId?: string,
    exportFormat?: string,
  ) =>
    invoke<{ ok: boolean; template_id: string }>("data_save_template", {
      name,
      description: description ?? null,
      taskId: taskId ?? null,
      exportFormat: exportFormat ?? "excel",
    }),

  /** POST /data/test_connection —— 测试数据库连接（主流+国产/信创） */
  dataTestConnection: (params: {
    dbType: string;
    host?: string;
    port?: number;
    database?: string;
    username?: string;
    password?: string;
    path?: string;
  }) =>
    invoke<{ ok: boolean; type: string; message: string }>(
      "data_test_connection",
      {
        dbType: params.dbType,
        host: params.host ?? null,
        port: params.port ?? null,
        database: params.database ?? null,
        username: params.username ?? null,
        password: params.password ?? null,
        path: params.path ?? null,
      },
    ),

  // ---- Phase 2F+ logviewer (Rust 端 10 Tauri command) ----

  /** Stat a file (size + mtime). Used by SmartFileOpener. */
  logviewerStatFile: (path: string) =>
    invoke<{ size: number; modified_secs: number }>("logviewer_stat_file", {
      path,
    }),

  /** Submit a file for line indexing. Returns task ID. */
  logviewerIndexFile: (path: string) =>
    invoke<string>("logviewer_index_file", { path }),

  /** Submit a search over an indexed file. Returns task ID. */
  logviewerSearch: (
    path: string,
    pattern: string,
    mode: "literal" | "regex",
    contextBefore: number,
    contextAfter: number,
    maxMatches: number,
    maxBytes: number,
  ) =>
    invoke<string>("logviewer_search", {
      path,
      pattern,
      mode,
      contextBefore,
      contextAfter,
      maxMatches,
      maxBytes,
    }),

  /** Read lines [start, end) from an indexed file. */
  logviewerReadLines: (
    path: string,
    startLine: number,
    endLine: number,
    maxBytes: number,
  ) =>
    invoke<{ lines: string[]; truncated: boolean; bytes_read: number }>(
      "logviewer_read_lines",
      { path, startLine, endLine, maxBytes },
    ),

  /** Poll a task's status. */
  logviewerTaskStatus: (taskId: string) =>
    invoke<{
      id: string;
      kind: "index" | "search";
      path: string;
      status: "queued" | "running" | "completed" | "failed" | "cancelled";
      created_at_unix_ms: number;
      finished_at_unix_ms: number | null;
      error: string | null;
      summary: {
        file_size: number;
        line_count: number;
        file_fingerprint: string;
        last_modified: number;
        indexed_at: number;
        encoding: string;
      } | null;
    } | null>("logviewer_task_status", { taskId }),

  /** Cancel a running/queued task. */
  logviewerCancelTask: (taskId: string) =>
    invoke<string>("logviewer_cancel_task", { taskId }),

  /** Check if a file has a usable index. */
  logviewerIndexStatus: (path: string) =>
    invoke<
      | { kind: "missing" }
      | { kind: "ready"; line_count: number; indexed_at: number }
    >("logviewer_index_status", { path }),

  // ---- Phase 2F+ V1.5 Tail -f ----

  /** Start tail -f on a file. Returns { session_id }. */
  logviewerTailStart: (path: string) =>
    invoke<{ session_id: string }>("logviewer_tail_start", { path }),

  /** Stop a tail session. Returns { stopped: boolean }. */
  logviewerTailStop: (sessionId: string) =>
    invoke<{ stopped: boolean }>("logviewer_tail_stop", { sessionId }),

  /** Get tail session info (null if not found). */
  logviewerTailStatus: (sessionId: string) =>
    invoke<{
      session_id: string;
      path: string;
      active: boolean;
      bytes_read: number;
      lines_read: number;
    } | null>("logviewer_tail_status", { sessionId }),

  /** List all active tail sessions. */
  logviewerTailList: () =>
    invoke<
      Array<{
        session_id: string;
        path: string;
        active: boolean;
        bytes_read: number;
        lines_read: number;
      }>
    >("logviewer_tail_list"),

  // ---- Phase 16 思维链可视化与文件操作追踪 ----

  /** GET /trace/sessions —— 最近会话列表（启动时自动加载最近会话思维链） */
  traceRecentSessions: () =>
    invoke<{
      count: number;
      sessions: Array<{ session_id: string; steps: number; last_ts: number }>;
    }>("trace_recent_sessions"),

  /** GET /trace/session/{session_id} —— 会话思维链时间线 */
  traceGetSession: (sessionId: string) =>
    invoke<TraceSessionResponse>("trace_get_session", { sessionId }),

  /** GET /trace/step/{step_id} —— 单步详情 */
  traceGetStep: (stepId: string) =>
    invoke<ThinkingStep>("trace_get_step", { stepId }),

  /** GET /trace/file-diff/{step_id}/{file_index} —— hover 懒加载完整 diff */
  traceGetFileDiff: (stepId: string, fileIndex: number) =>
    invoke<TraceFileDiffResponse>("trace_get_file_diff", { stepId, fileIndex }),

  // ---- Phase 15 V0: 前端实时预览引擎 ----

  /** POST /preview/start —— 启动预览会话（Python 后端起 Vite 子进程） */
  previewStart: (body: {
    projectPath: string;
    entryFile?: string;
    framework?: PreviewFramework;
    port?: number;
    /** BUGFIX #175：白名单拒绝后用户确认加入，带 true 重试 */
    allowPath?: boolean;
  }) =>
    invoke<PreviewSession>("preview_start", {
      projectPath: body.projectPath,
      entryFile: body.entryFile,
      framework: body.framework,
      port: body.port,
      allowPath: body.allowPath,
    }),

  /** POST /preview/stop/{session_id} —— 停止预览会话 */
  previewStop: (sessionId: string) =>
    invoke<PreviewSession>("preview_stop", { sessionId }),

  /** GET /preview/sessions —— 活跃会话列表 */
  previewSessions: () => invoke<PreviewSession[]>("preview_sessions"),

  /** GET /preview/info/{session_id} —— 会话详情 */
  previewInfo: (sessionId: string) =>
    invoke<PreviewSession>("preview_info", { sessionId }),

  /** POST /preview/reload/{session_id} —— 强制刷新 */
  previewReload: (sessionId: string) =>
    invoke<PreviewSession>("preview_reload", { sessionId }),

  /** POST /preview/install/{session_id} —— 手动触发依赖安装 */
  previewInstall: (sessionId: string) =>
    invoke<{ session_id: string; install_started: boolean }>(
      "preview_install",
      {
        sessionId,
      },
    ),

  /** 打开独立预览窗口（主路径 WebviewWindow） */
  previewOpenWindow: (
    sessionId: string,
    url: string,
    deviceMode: PreviewDeviceMode = "desktop",
  ) =>
    invoke<string>("preview_open_window", {
      sessionId,
      url,
      deviceMode,
    }),

  /** 关闭预览窗口 */
  previewCloseWindow: (sessionId: string) =>
    invoke<boolean>("preview_close_window", { sessionId }),

  /** 刷新预览窗口页面 */
  previewReloadWindow: (sessionId: string) =>
    invoke<boolean>("preview_reload_window", { sessionId }),

  /** 调整预览窗口尺寸（设备模式切换） */
  previewResizeWindow: (sessionId: string, deviceMode: PreviewDeviceMode) =>
    invoke<boolean>("preview_resize_window", { sessionId, deviceMode }),

  /** 列出全部预览窗口 label */
  previewListWindows: () => invoke<string[]>("preview_list_windows"),

  // ---- V9 Office 预览（OfficeCLI 渲染 docx/xlsx/pptx → HTML/PNG，2026-08-25）----

  /**
   * 渲染 Office 文件为预览产物。html 模式返 {html} 渲染页全文（srcDoc 展示）；
   * screenshot 模式返 {image_base64, page}。经 Rust 代理（WebView 受 CSP 不能直连 8765）。
   */
  officePreviewRender: (
    path: string,
    mode: 'html' | 'screenshot' = 'html',
    page?: number,
  ) =>
    invoke<{
      ok: boolean;
      session_id: string;
      mode: 'html' | 'screenshot';
      html?: string;
      html_url?: string;
      image_base64?: string;
      page?: number;
    }>("office_preview_render", { path, mode, page }),

  /** 停止 Office 预览会话（后端清理临时目录；best-effort） */
  officePreviewStop: (sessionId: string) =>
    invoke<{ ok: boolean; stopped: boolean }>("office_preview_stop", { sessionId }),

  // ---- MCP 服务器配置（设置页「MCP」面板）----

  /** GET /mcp-config —— 读取 mcp.yaml 注册表 */
  mcpConfigGet: () => invoke<McpConfigResponse>("mcp_config_get"),

  /** PUT /mcp-config —— 整表覆盖保存（Agent 侧校验 + 原子写盘） */
  mcpConfigSave: (servers: Record<string, McpServerSpec>) =>
    invoke<{ ok: boolean; servers: Record<string, McpServerSpec> }>(
      "mcp_config_save",
      { servers },
    ),

  /** POST /mcp-config/test —— 对单条配置做真实 stdio 握手 + list_tools */
  mcpConfigTest: (entry: { name: string } & McpServerSpec) =>
    invoke<McpTestResult>("mcp_config_test", { entry }),

  /** POST /mcp-config/reload —— 重读 mcp.yaml 并重建运行中连接 */
  mcpConfigReload: () =>
    invoke<{ ok: boolean; servers: string[] }>("mcp_config_reload"),
} as const;
