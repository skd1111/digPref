/**
 * Phase 12 V1.5 —— 多智能体调度子 Agent 类型镜像
 *
 * TS ↔ Python（services/agent/src/agent/orchestrator/spec.py）双侧镜像。
 * 任何字段变更必须同步两个文件（CLAUDE.md §6 红线类比 —— 类型协议也算契约）。
 *
 * 文档：[docs/design/phase-12-multi-agent-scaling.md](../../../docs/design/phase-12-multi-agent-scaling.md)
 */

export type SubAgentStatus =
  | 'pending'    // 已派单，未启动
  | 'running'    // LLM 调用中
  | 'ok'         // 完成 + 校验通过
  | 'err'        // 异常 / LLM 调用失败 / 校验失败
  | 'dlq'        // 重试 3 次仍失败 → 进死信
  | 'cancelled'; // 用户主动取消

export type ContextStrategy =
  | 'passthrough'           // 轻量透传（≤ 200 token）
  | 'shared_memory_pool'    // 共享记忆池（中等协作）
  | 'incremental_summary';  // 摘要 + 增量（长会话 / 复杂任务）

export type ModelRole = 'utility' | 'reasoning' | 'execution';

export type LocalOnlyTask = 'intent' | 'repair' | 'data_summary' | 'local_intent' | 'biznav_extract' | 'log_level_classify' | 'image_processing_summary' | 'builtin_tool_summary' | 'builtin_search_summarize' | 'decompose' | 'tool_orchestrate';

export type SubAgentTaskType =
  | 'intent'
  | 'repair'
  | 'data_summary'
  | 'plan'
  | 'summarise'
  | 'custom';

export interface ContextPolicy {
  strategy: ContextStrategy;
  /** 必读字段不可压（CLAUDE.md §1 铁律 5） */
  required_fields: string[];
  /** 共享记忆池（中等协作用） */
  shared_keys: string[];
  /** 摘要最大 token（长会话用） */
  max_summary_tokens: number;
}

export interface ModelPolicy {
  role: ModelRole;
  task_type: SubAgentTaskType;
  /** 敏感负载标记：携带 DB 行 / SQL 错误 / PII → 强制本地 */
  carries_sensitive_payload: boolean;
  /** 用户显式指定 backend（空 = 走 router 默认） */
  preferred_backend: string | null;
}

export interface SubAgentSpec {
  spec_version: number;
  sub_agent_id: string;
  parent_run_id: string;
  parent_sub_agent_id: string | null;
  /** 派生深度（0 = 主图；1 = 一级子；2 = 二级子；上限 = 2） */
  depth: number;
  task_type: string;
  task_description: string;
  input_payload: Record<string, unknown>;
  context_policy: ContextPolicy;
  model_policy: ModelPolicy;
  /** 写操作标记：true → 子 Agent 写操作必须回主图 hitl_gate */
  requires_write: boolean;
}

export interface ArtifactRef {
  artifact_id: string;
  kind: 'summary' | 'raw_text' | 'table' | 'chart_spec' | 'code_diff';
  content_hash: string;
  byte_size: number;
  preview: string;
}

export interface StateDelta {
  fields_added: Record<string, unknown>;
  fields_modified: Record<string, unknown>;
  raw_refs: ArtifactRef[];
}

export interface SubAgentReport {
  spec_version: number;
  sub_agent_id: string;
  parent_run_id: string;
  parent_sub_agent_id: string | null;
  status: SubAgentStatus;
  started_at: string;
  finished_at: string | null;
  summary: string;
  confidence: number;
  state_delta: StateDelta;
  artifacts: ArtifactRef[];
  backend_used: string;
  model_used: string;
  latency_ms: number;
  error_message: string;
  attempts: number;
}

// ---- 决策树回放 ----------------------------------------------------------

export type SubAgentEventType =
  | 'sub_agent_spawn'
  | 'sub_agent_progress'
  | 'sub_agent_done'
  | 'sub_agent_retry'
  | 'sub_agent_dlq'
  | 'sub_agent_cancel'
  | 'sub_agent_closed'
  | 'sub_agent_requeued'
  | 'sub_agent_hitl_requested'
  | 'sub_agent_hitl_decided'
  | 'sub_agent_judge';

export interface SubAgentAuditEvent {
  id: number;
  ts: string;
  event_type: SubAgentEventType;
  actor_type: 'user' | 'main_agent' | 'sub_agent' | 'system';
  task_id: string | null;
  parent_task_id: string | null;
  run_id: string | null;
  payload: Record<string, unknown>;
}

export interface SubAgentReplayTreeNode {
  task_id: string | null;
  parent_task_id: string | null;
  events: SubAgentAuditEvent[];
  children: SubAgentReplayTreeNode[];
}

export interface SubAgentReplaySummary {
  correlation_id: string;
  event_count: number;
  task_count: number;
  by_event_type: Record<string, number>;
  has_dlq: boolean;
  has_cancel: boolean;
  replayable: boolean;
}

// ---- 评测指标 ------------------------------------------------------------

export interface SubAgentMetricsSnapshot {
  dispatched: number;
  succeeded: number;
  failed: number;
  dlq: number;
  cancelled: number;
  retries: number;
  success_rate: number;
  retry_rate: number;
  dlq_rate: number;
  validation_pass_rate: number;
  compression_ratio: number;
  required_fields_kept_rate: number;
  p50_ms: number;
  p99_ms: number;
  local_only_forced: number;
  hitl: { requested: number; approved: number; rejected: number };
  judge: {
    samples: number;
    avg_score: number;
    sample_rate: number;
    is_ci_gate: false;        // 设计文档 §3.3：Judge 不作 CI 闸门
  };
  thresholds: Record<string, number>;
  violations: string[];
}

// ---- 队列 / 派生树 / DLQ 统计 ---------------------------------------------

export interface SubAgentQueueStats {
  closed: boolean;
  pending: number;
  by_priority: { high: number; normal: number; low: number };
  enqueued_total: number;
  dequeued_total: number;
  dedup_hits: number;
  backlog_alert: boolean;
}

export interface SubAgentTreeStats {
  total_nodes: number;
  max_depth: number;
  max_total_nodes: number;
  headroom_depth: number;
  headroom_nodes: number;
}

export interface SubAgentDLQItem {
  task_id: string;
  correlation_id: string;
  idempotency_token: string;
  payload_json: string;
  last_error: string;
  attempts: number;
  state: 'open' | 'requeued' | 'closed';
  note: string | null;
  handled_by: string | null;
  enqueued_at: string;
  handled_at: string | null;
}
