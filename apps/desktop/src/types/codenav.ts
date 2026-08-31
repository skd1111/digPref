/**
 * codenav.ts —— Phase 2F 代码阅读与 AI 导航前端类型（V0 前端独有，不进 shared-protocol）。
 *
 * V1 接 FastAPI 时本文件作为前后端契约的 TS 镜像（与 Python `codenav/models.py` 对齐）。
 *
 * 范围（V0 MVP 前端 mock）：
 *   - 5 类 kind + 4 种语言
 *   - 符号 + 跳转结果 + 索引状态
 *
 * 设计文档：[docs/design/phase-2f-code-nav.md](../docs/design/phase-2f-code-nav.md)
 */

// ---------- 枚举 ----------

export type SymbolKind =
  | 'class'
  | 'method'
  | 'function'
  | 'interface'
  | 'field'
  | 'enum';

export type Language =
  | 'java'
  | 'python'
  | 'typescript'
  | 'javascript'
  | 'vue'
  | 'go'
  | 'c'
  | 'cpp'
  | 'csharp'
  | 'php'
  | 'ruby'
  | 'rust'
  | 'kotlin'
  | 'swift'
  | 'scala';

// ---------- Symbol ----------

export interface Symbol {
  id: string;
  name: string;
  kind: SymbolKind;
  /** 仓库相对路径（如 `apps/desktop/src/views/AuditDashboard.tsx`） */
  file_path: string;
  start_line: number;
  end_line: number;
  /** 完整签名（如 `export function AuditDashboard(): JSX.Element`） */
  signature: string | null;
  /** 所属类名（method / field 专用，class / function 留 null） */
  parent_class: string | null;
  language: Language;
  /** os.stat().st_mtime 时间戳（V0 mock 写死 NOW - x） */
  last_modified: number;
  /** 实际代码片段（V0 mock 内联；V1 由后端按需 fetch） */
  snippet: string;
}

// ---------- 跳转结果 ----------

export type JumpSource = 'local_index' | 'ai_inference';

export interface JumpResult {
  symbol_id: string;
  file_path: string;
  line: number;
  /** 1.0 = 本地索引命中, <1.0 = AI 推断 */
  confidence: number;
  source: JumpSource;
  /** AI 推断时的补充说明 */
  note: string | null;
}

// ---------- 索引状态 ----------

export interface IndexStatus {
  total_files: number;
  total_symbols: number;
  last_full_scan: number | null;
  last_incremental: number | null;
  is_scanning: boolean;
}

// ---------- AI 解释（V0 mock） ----------

export interface AiExplanation {
  symbol_id: string;
  /** "🤖 AI 推断" 文本 */
  text: string;
  /** 置信度 0.0-1.0 */
  confidence: number;
  /** 模拟生成耗时（V0 mock 写死 800-1500ms） */
  latency_ms: number;
  created_at: number;
}

// ---------- UI 视觉规范 ----------

export const KIND_COLORS: Record<SymbolKind, { bg: string; fg: string; label: string }> = {
  class:     { bg: '#795e26', fg: '#0e0e0e', label: '类' },
  method:    { bg: '#0451a5', fg: '#ffffff', label: '方法' },
  function:  { bg: '#059669', fg: '#0e0e0e', label: '函数' },
  interface: { bg: '#c586c0', fg: '#0e0e0e', label: '接口' },
  field:     { bg: '#b25c1a', fg: '#0e0e0e', label: '字段' },
  enum:      { bg: '#b5cea8', fg: '#0e0e0e', label: '枚举' },
};

export const LANGUAGE_COLORS: Record<Language, { bg: string; fg: string; label: string }> = {
  java:       { bg: '#cd3131', fg: '#0e0e0e', label: 'Java' },
  python:     { bg: '#3776ab', fg: '#ffffff', label: 'Python' },
  typescript: { bg: '#007acc', fg: '#ffffff', label: 'TS' },
  javascript: { bg: '#f7df1e', fg: '#0e0e0e', label: 'JS' },
  vue:        { bg: '#42b883', fg: '#0e0e0e', label: 'Vue' },
  go:         { bg: '#00add8', fg: '#ffffff', label: 'Go' },
  c:          { bg: '#555555', fg: '#ffffff', label: 'C' },
  cpp:        { bg: '#00599c', fg: '#ffffff', label: 'C++' },
  csharp:     { bg: '#68217a', fg: '#ffffff', label: 'C#' },
  php:        { bg: '#777bb3', fg: '#0e0e0e', label: 'PHP' },
  ruby:       { bg: '#cc342d', fg: '#ffffff', label: 'Ruby' },
  rust:       { bg: '#dea584', fg: '#0e0e0e', label: 'Rust' },
  kotlin:     { bg: '#7f52ff', fg: '#ffffff', label: 'Kotlin' },
  swift:      { bg: '#f05138', fg: '#ffffff', label: 'Swift' },
  scala:      { bg: '#dc322f', fg: '#ffffff', label: 'Scala' },
};
