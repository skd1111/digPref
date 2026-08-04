/**
 * Phase 2F 代码导航 —— TypeScript ↔ Python 类型镜像。
 *
 * V1 收尾（2026-07-29）：shared-protocol 加 codenav 模块，统一前端/Rust/Python
 * 三端类型。前端用 ipc.codeNavJump / codeNavListSymbols 返回类型；Rust
 * commands/codenav.rs 序列化时对齐；Python codenav.models 对齐字段。
 *
 * 与 Python `services/agent/src/agent/codenav/models.py` 字段保持一致；
 * 任何字段增减必须三处同步更新（CLAUDE.md §"SSE 事件名 = Tauri 通道名"
 * 的同样纪律适用于此）。
 */

export type Language =
  | 'java'
  | 'python'
  | 'typescript'
  | 'javascript'
  | (string & {}); // 允许后端扩展（language_registry 可能加更多）

export type SymbolKind =
  | 'class'
  | 'interface'
  | 'method'
  | 'function'
  | 'field'
  | 'enum'
  | 'variable'
  | (string & {});

export interface Symbol {
  id?: number;
  /** 符号名（如 calculateInterest / OrderService） */
  name: string;
  kind: SymbolKind;
  /** 绝对路径 */
  file_path: string;
  /** 起始行号 (1-indexed) */
  start_line: number;
  /** 结束行号 */
  end_line: number;
  /** 完整签名（如 `public BigDecimal calculateInterest(...)`） */
  signature?: string | null;
  /** 所属类名（方法/字段专用） */
  parent_class?: string | null;
  language: Language;
  /** os.stat().st_mtime（增量校验） */
  last_modified?: number;
}

/** 跳转结果（ipc.codeNavJump 返回） */
export interface JumpResult {
  file_path: string;
  line: number;
  /** 0-1 置信度（搜索或 AI 推断） */
  confidence: number;
  /** 'local_index' = SQLite 命中；'not_found' = 兜底 */
  source: 'local_index' | 'not_found';
  /** AI 推断的 note（可选） */
  note?: string | null;
}

/** AI 解释结果（ipc.codeNavExplain 返回） */
export interface ExplainResult {
  symbol: string;
  text: string;
  /** 用了哪个 backend（'ollama' / 'private' / 'mock'） */
  backend: string;
  /** 解释来源（'local' = 读源文件；'llm' = LLM 推断） */
  source: 'local' | 'llm';
  latency_ms: number;
}

/** 索引状态（ipc.codeNavStatus 返回） */
export interface IndexStatus {
  total_files: number;
  total_symbols: number;
  /** 最近一次全量扫描时间戳（毫秒） */
  last_full_scan: number | null;
  /** 最近一次增量更新时间戳（毫秒） */
  last_incremental: number | null;
  is_scanning: boolean;
}

/** IndexRequest body（ipc.codeNavIndex 调用参数） */
export interface IndexRequest {
  /** 多根目录扫描 */
  root_paths?: string[] | null;
  /** 增量加入根目录（不删除现有） */
  add_roots?: string[] | null;
  /** 只扫描指定文件列表 */
  files?: string[] | null;
}

/** ExplainRequest body（ipc.codeNavExplain 调用参数） */
export interface ExplainRequest {
  symbol: string;
  /** 当前所在文件（用于上下文） */
  current_file?: string;
  /** 上下文代码片段 */
  context?: string;
  /** 当前所在行 */
  line?: number;
}

/** 索引符号搜索（ipc.codeNavListSymbols 调用参数） */
export interface ListSymbolsRequest {
  name: string;
  kind?: SymbolKind;
  limit?: number;
}

/** LLM 后端配置（ipc.codeNavLlmBackend 调用返回） */
export interface LlmBackend {
  name: string;
  base_url: string;
  model: string;
  /** 当前绑定的 backend（'ollama' / 'private' / null = 自动） */
  bound_to?: string | null;
}

/** LLM Config（ipc.codeNavLlmConfig 调用返回） */
export interface LlmConfig {
  backend: string;
  model: string;
  max_context: number;
  temperature?: number;
}

/** 允许扫描的根目录列表（ipc.codeNavAllowedRoots 调用返回） */
export interface AllowedRoots {
  roots: string[];
  /** 来源（如 'env:EAIDE_ALLOWED_ROOTS' / 'settings'） */
  extra_env: string;
}