/**
 * DSpark 推测解码契约 — TS 镜像。
 *
 * 注意：本模块使用 **snake_case**（与 FastAPI 响应 / dspark.json 一致），
 * 不走 shared-protocol 默认的 camelCase 别名约定。原因：
 *   1. DSpark V0 API + 持久化文件 + 前端已全部用 snake_case
 *   2. 加 alias 转换会破坏现有测试与持久化兼容性
 *   3. 类型对齐成本 > 风格统一成本
 *
 * 如果 Phase 14 决定全局改名，DSpark 可以单独走 alias 路径，不影响其它模块。
 *
 * 文档：[docs/design/phase-13-dspark.md](../../../../docs/design/phase-13-dspark.md)
 */

// === 校验上下界常量（前后端共用唯一真源）==================================

export const DSPARK_CONTEXT_SIZE_MIN = 512;
export const DSPARK_CONTEXT_SIZE_MAX = 262144;
export const DSPARK_CONTEXT_SIZE_DEFAULT = 4096;

export const DSPARK_GPU_LAYERS_MIN = -1;
export const DSPARK_GPU_LAYERS_MAX = 999;
export const DSPARK_GPU_LAYERS_DEFAULT = 0;

export const DSPARK_SHORT_OUTPUT_MIN = 1;
export const DSPARK_SHORT_OUTPUT_DEFAULT = 20;

export const DSPARK_N_DRAFT_MIN = 1;
export const DSPARK_N_DRAFT_MAX = 16;

export const DSPARK_DRAFT_P_MIN_DEFAULT_AGGRESSIVE = 0.75;
export const DSPARK_DRAFT_P_MIN_DEFAULT_STANDARD = 0.85;
export const DSPARK_DRAFT_P_MIN_DEFAULT_CONSERVATIVE = 0.9;
export const DSPARK_DRAFT_P_MIN_DEFAULT_OFF = 1.0;

// === 字面量类型 ============================================================

export type SpeculativeMode = 'aggressive' | 'standard' | 'conservative' | 'off';

export type DSparkDecisionReason =
  | 'applied'
  | 'applied-default'
  | 'off-global'
  | 'off-local-only'
  | 'off-short'
  | 'off-no-draft'
  | 'off-no-runtime';

// === 模型 ==================================================================

export interface SpeculativePolicy {
  task_category: string;
  mode: SpeculativeMode;
  n_draft: number;
  draft_p_min: number;
  enabled?: boolean; // 仅 GET /dspark/policies 响应携带，POST body 不需要
}

export interface DSparkConfig {
  draft_model_path: string | null;
  short_output_threshold: number;
  enable_global: boolean;
  context_size: number;
  gpu_layers: number;
}

/** GET /dspark/config 响应：DSparkConfig + 服务端统计 + 路径信息 */
export interface DSparkRuntimeConfig extends DSparkConfig {
  yaml_path: string | null;
  profile_count: number;
  stats: {
    total_decisions: number;
    dspark_enabled_pct: number;
    per_category: Record<string, number>;
    per_reason: Record<string, number>;
  };
}

/** GET /dspark/recent 单条记录（engine deque，UI 必须显示"本次会话"）*/
export interface DSparkDecisionRecord {
  ts: number;
  task_category: string;
  speculative_enabled: boolean;
  n_draft: number;
  draft_p_min: number;
  backend: string;
  reason: DSparkDecisionReason;
  max_tokens: number;
}

/** POST /dspark/config 请求体（所有字段可选，只更新非 null 字段）*/
export interface DSparkConfigUpdateBody {
  draft_model_path?: string | null;
  short_output_threshold?: number;
  enable_global?: boolean;
  context_size?: number;
  gpu_layers?: number;
}

/** POST /dspark/draft-model-path 请求体 */
export interface DSparkDraftModelPathBody {
  path: string | null;
}

// === 工具 ==================================================================

/** Mode → 默认 K/阈值映射（4 档预设） */
export const MODE_PARAMS: Record<SpeculativeMode, { n_draft: number; draft_p_min: number }> = {
  aggressive: {
    n_draft: 8,
    draft_p_min: DSPARK_DRAFT_P_MIN_DEFAULT_AGGRESSIVE,
  },
  standard: {
    n_draft: 4,
    draft_p_min: DSPARK_DRAFT_P_MIN_DEFAULT_STANDARD,
  },
  conservative: {
    n_draft: 2,
    draft_p_min: DSPARK_DRAFT_P_MIN_DEFAULT_CONSERVATIVE,
  },
  off: {
    n_draft: 1,
    draft_p_min: DSPARK_DRAFT_P_MIN_DEFAULT_OFF,
  },
};

/** 决策原因 → 颜色（与 Python REASON_COLOR 一一对应） */
export const REASON_COLOR: Record<DSparkDecisionReason, string> = {
  applied: '#4ec9b0',
  'applied-default': '#dcdcaa',
  'off-global': '#858585',
  'off-local-only': '#f48771',
  'off-short': '#dcdcaa',
  'off-no-draft': '#858585',
  'off-no-runtime': '#f48771',
};

/** 模式 → 颜色（4 档配色） */
export const MODE_COLOR: Record<SpeculativeMode, string> = {
  aggressive: '#dcdcaa',
  standard: '#569cd6',
  conservative: '#4ec9b0',
  off: '#858585',
};