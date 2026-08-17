/**
 * Phase 7 v2.87 · 数据专家模式 —— TypeScript ⇄ Python 类型镜像。
 *
 * 与 Python ``services/agent/src/agent/dataexpert/metric_resolver.py`` 字段保持一致；
 * 任何字段增减必须**三处同步**（Python Pydantic + TS interface + Rust serde）：
 *
 *   Python: services/agent/src/agent/dataexpert/metric_resolver.py::MetricDef / ResolvedQuery
 *   TS:     packages/shared-protocol/src/ts/dataexpert.ts（本文件）
 *   FastAPI: services/agent/src/agent/dataexpert/api.py::MetricResolveResponse
 *
 * 设计原则（CLAUDE.md §6）：
 *   - 字段名 + 字段类型与 Pydantic v2 model_dump() 输出严格对齐
 *   - Literal 类型用 TS 字面量联合 + (string & {}) 兜底（允许后端扩展）
 *   - 时间戳用 number（Unix epoch ms，与前端 Date 互转）
 *
 * 前端用法：
 *   import type { MetricDef, ResolvedQuery, NL2SQLResponse } from '@shared/dataexpert';
 *
 *   const r = await fetch('/data/nl2sql', { ... });
 *   const body: NL2SQLResponse = await r.json();
 *   console.log(body.metric_source_kind);  // "dict" | "platform" | "bridge" | ""
 */

// ---- Literal 类型 ------------------------------------------------------------

/** v2.87 MetricResolver 来源种类（决定 SQL 怎么生成） */
export type SourceKind = 'dict' | 'platform' | 'bridge' | (string & {});

/** v2.87 MetricDef 聚合方式 */
export type AggKind =
  | 'SUM'
  | 'AVG'
  | 'COUNT'
  | 'MIN'
  | 'MAX'
  | 'COUNT_DISTINCT'
  | 'RAW'
  | (string & {});

// ---- MetricDef（指标定义） -----------------------------------------------------

/**
 * 指标定义 —— 单一指标的元数据。
 *
 * 对应 Python ``MetricDef``。字段 frozen（前端不要直接修改后端返回的实例）。
 */
export interface MetricDef {
  /** 指标编码（业务主键） */
  code: string;
  /** 中文名（如"放款总额"） */
  name: string;
  /** 业务定义（可选） */
  description?: string;
  /** 源表名（如"dws.loan_fact"） */
  source_table: string;
  /** 源列名（如"fwd_amt"） */
  source_column: string;
  /** 聚合方式 */
  agg: AggKind;
  /** 可选维度列表 */
  dimensions: string[];
  /** 指标 owner（指标平台才有） */
  owner?: string | null;
  /** 维度值映射（如 {"vip_level": "GOLD"}） */
  dimension_mappings: Record<string, string>;
}

// ---- ResolvedQuery（自然语言解析后的结构化查询意图） ----------------------------

/**
 * 自然语言解析后的结构化查询意图。
 *
 * ``source_kind`` 决定下游 NL2SQL 节点走哪条分支：
 *   - dict     —— V0 自拼 SQL（基于 ``metric.source_table``）
 *   - platform —— 平台给的 SQL 模板（``platform_sql`` 字段非空）
 *   - bridge   —— 走 dws 视图间接查询
 */
export interface ResolvedQuery {
  metric: MetricDef;
  /** 用户问句里的维度筛选（{region: "华东", vip_level: "GOLD"}） */
  dimensions_filter: Record<string, string>;
  /** 时间范围 [start, end)（ISO 8601 字符串） */
  time_range?: [string, string] | null;
  source_kind: SourceKind;
  /** 识别置信度（0-1） */
  confidence: number;
  /** platform 模式的 SQL 模板（V1 PlatformMetricResolver 才有） */
  platform_sql?: string | null;
  /** Platform 模式 top-K 候选指标（前端让用户选） */
  candidates: MetricDef[];
}

// ---- API 请求 / 响应 --------------------------------------------------------

/**
 * POST /data/metric/resolve 请求体。
 */
export interface MetricResolveRequest {
  /** 用户自然语言问句（1-2048 字符） */
  question: string;
  /** 数据源 ID（可选） */
  source_id?: string;
}

/**
 * POST /data/metric/resolve 响应。
 *
 * ``resolved`` 为 null 表示识别失败（前端可回退到纯 NL2SQL）。
 */
export interface MetricResolveResponse {
  resolved?: ResolvedQuery | null;
  /** 错误场景：配置错误 / Platform 缺 base_url */
  error?: string;
}

/**
 * GET /data/metric/list 响应。
 */
export interface MetricListResponse {
  /** MetricDef 列表 */
  metrics: MetricDef[];
  /** 当前 resolver 类型 */
  source_kind: SourceKind;
}

/**
 * POST /data/nl2sql 响应（v2.87 增量：加 metric_source_kind + metric_confidence 字段）。
 */
export interface NL2SQLResponse {
  sql: string;
  is_heavy: boolean;
  tables_used: string[];
  dictionary_context: string;
  error?: string;
  /** v2.87 MetricResolver 透传：让前端 DataWorkbench 状态栏显示当前 resolver 类型 */
  metric_source_kind?: SourceKind | '';
  /** v2.87 识别置信度（0-1） */
  metric_confidence?: number;
}

// ---- 配置文件类型（v2.87 config/data_expert.yaml） -----------------------------

/**
 * config/data_expert.yaml::metric_resolver 段 TypeScript 镜像。
 *
 * ⚠️ V0 现状（2026-08-14 勘误）：yaml 是预留配置模板，当前无代码读取；
 * 后端 ``build_resolver()`` 仅读入参 + ``EAIDE_METRIC_RESOLVER`` 环境变量选实现（yaml 接线 V1 接力）。
 * 前端“指标浏览器”状态栏读 ``source_kind`` 字段展示当前 resolver 类型。
 */
export interface MetricResolverConfig {
  /** 类型：dict / platform / bridge（V0 默认 dict） */
  type: SourceKind;
  /** DictMetricResolver 配置 */
  dict: {
    /** 业务字典 YAML 目录（V1 外置） */
    dict_path: string;
  };
  /** PlatformMetricResolver 配置（V1 接力） */
  platform: {
    /** 指标平台 API base URL */
    base_url: string;
    /** Keyring 占位符 / 环境变量名 */
    auth_secret: string;
    /** HTTP 调用超时（秒） */
    timeout: number;
  };
  /** BridgeMetricResolver 配置（V1.5 接力） */
  bridge: {
    /** dws schema 名 */
    dws_schema: string;
  };
}

/**
 * 完整 config/data_expert.yaml 镜像。
 */
export interface DataExpertConfig {
  data_expert_db_path: string;
  data_result_dir: string;
  data_sql_row_limit: number;
  data_sandbox_mem_mb: number;
  data_sandbox_timeout: number;
  data_export_watermark: boolean;
  data_require_mask_on_export: boolean;
  /** v2.87 新增段 */
  metric_resolver: MetricResolverConfig;
}