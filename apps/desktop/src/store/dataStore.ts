/**
 * dataStore —— 数据专家工作台状态（Phase 7 V1，真接 Tauri commands）。
 *
 * 范围：
 *   - 数据源（后端 data_expert.db 加载）+ 表结构 + 数据字典（字段中文注释）
 *   - 历史分析（后端 analysis_tasks 表）
 *   - 编辑器三模式（SQL / Python / 对话）+ AI 对话消息
 *   - 查询结果集（列 + 行）+ 图表配置
 *
 * V1 真接：所有 actions 调用 ipc.data* 命令（Rust → Python Agent /data/* 路由）。
 * ⚠ 只读铁律：后端 readonly/guard.py 硬拦截任何写操作。
 */
import { create } from 'zustand';
import { ipc } from '@/ipc/invoke';

// ---- 类型 ------------------------------------------------------------------

export type SourceType = 'mysql' | 'oracle' | 'csv' | 'excel';
export type EditorMode = 'sql' | 'python' | 'chat';
export type ChartType = 'bar' | 'line' | 'pie' | 'scatter';

export interface Column {
  name: string;
  type: string;
  comment: string;
}

export interface TableSchema {
  name: string;
  comment: string;
  columns: Column[];
}

export interface DataSource {
  id: string;
  name: string;
  type: SourceType;
  status: 'connected' | 'offline';
  tables: TableSchema[];
}

export interface HistoryAnalysis {
  id: string;
  name: string;
  createdAt: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface QueryResult {
  columns: string[];
  rows: Array<Array<string | number>>;
  rowCount: number;
  elapsedMs: number;
  recommendedChart: ChartType;
  chartXIndex: number;
  chartYIndex: number;
  truncated?: boolean;
}

// ---- Store -----------------------------------------------------------------

interface DataState {
  sources: DataSource[];
  history: HistoryAnalysis[];
  selectedSourceId: string;
  selectedTable: string | null;
  loading: boolean;
  error: string | null;

  editorMode: EditorMode;
  sqlText: string;
  pythonText: string;
  chat: ChatMessage[];
  chatDraft: string;

  running: boolean;
  result: QueryResult | null;
  chartType: ChartType;
  exporting: boolean;

  // Actions
  fetchSources: () => Promise<void>;
  selectSource: (id: string) => void;
  selectTable: (name: string | null) => void;
  setEditorMode: (m: EditorMode) => void;
  setSql: (v: string) => void;
  setPython: (v: string) => void;
  setChatDraft: (v: string) => void;
  sendChat: () => Promise<void>;
  runQuery: () => Promise<void>;
  runPython: () => Promise<void>;
  setChartType: (t: ChartType) => void;
  loadHistory: (id: string) => void;
  doExport: (fmt: string) => Promise<string | null>;
  saveTemplate: (name: string) => Promise<boolean>;
}

/** 判断是否只读 SELECT（前端提示用；后端 guard 硬拦截） */
export function isReadOnlySql(sql: string): boolean {
  const s = sql.replace(/--.*$/gm, '').toLowerCase();
  return !/\b(update|delete|drop|truncate|insert|alter|grant|revoke|create|replace|merge)\b/.test(s);
}

export const useDataStore = create<DataState>((set, get) => ({
  sources: [],
  history: [],
  selectedSourceId: '',
  selectedTable: null,
  loading: false,
  error: null,

  editorMode: 'sql',
  sqlText: '',
  pythonText: '# 受限沙箱执行（白名单 pandas/numpy/math/datetime）\nimport pandas as pd\n\ndf = load_result()  # 上一步 SQL 结果\nprint(df.describe())\n',
  chat: [],
  chatDraft: '',

  running: false,
  result: null,
  chartType: 'bar',
  exporting: false,

  // 从后端加载数据源列表
  fetchSources: async () => {
    set({ loading: true, error: null });
    try {
      const raw = await ipc.dataListSources();
      const sources: DataSource[] = raw.map((s) => ({
        id: s.id,
        name: s.name,
        type: (s.type as SourceType) || 'mysql',
        status: 'connected' as const,
        tables: (s.schema_cache || []).map((t) => ({
          name: t.name,
          comment: t.comment || '',
          columns: (t.columns || []).map((c) => ({
            name: c.name,
            type: c.dtype || '',
            comment: c.comment || '',
          })),
        })),
      }));
      set({
        sources,
        loading: false,
        selectedSourceId: sources.length > 0 ? sources[0].id : '',
      });
    } catch (e: unknown) {
      set({ loading: false, error: String(e) });
    }
  },

  selectSource: (id) => set({ selectedSourceId: id, selectedTable: null }),
  selectTable: (name) => set({ selectedTable: name }),
  setEditorMode: (m) => set({ editorMode: m }),
  setSql: (v) => set({ sqlText: v }),
  setPython: (v) => set({ pythonText: v }),
  setChatDraft: (v) => set({ chatDraft: v }),

  // 对话模式：NL2SQL 真接后端
  sendChat: async () => {
    const draft = get().chatDraft.trim();
    if (!draft) return;
    const sourceId = get().selectedSourceId;
    set((s) => ({
      chatDraft: '',
      chat: [...s.chat, { role: 'user', content: draft }],
      running: true,
    }));
    try {
      const res = await ipc.dataNl2sql(draft, sourceId || undefined);
      set((s) => ({
        running: false,
        sqlText: res.sql,
        editorMode: 'sql',
        chat: [
          ...s.chat,
          {
            role: 'assistant',
            content: `已生成 SQL（使用表：${res.tables_used.join(', ')}）。\n${res.dictionary_context ? '业务字典：' + res.dictionary_context + '\n' : ''}请复核后点「执行」。`,
          },
        ],
      }));
    } catch (e: unknown) {
      set((s) => ({
        running: false,
        chat: [...s.chat, { role: 'assistant', content: `❌ NL2SQL 失败：${String(e)}` }],
      }));
    }
  },

  // SQL 执行：真接后端
  runQuery: async () => {
    const { sqlText, selectedSourceId } = get();
    if (!sqlText.trim()) return;
    set({ running: true, error: null });
    try {
      const res = await ipc.dataRunSql(sqlText, selectedSourceId || undefined);
      if (!res.ok) {
        set({ running: false, error: res.error || '执行失败' });
        return;
      }
      // 图表推荐
      let chartType: ChartType = 'bar';
      let chartXIndex = 0;
      let chartYIndex = res.columns.length > 1 ? 1 : 0;
      try {
        const reco = await ipc.dataChartRecommend(res.columns, res.dtypes, res.row_count);
        chartType = (reco.chart_type as ChartType) || 'bar';
        chartXIndex = reco.x_index;
        chartYIndex = reco.y_index;
      } catch { /* 推荐失败不影响主流程 */ }
      set({
        running: false,
        result: {
          columns: res.columns,
          rows: res.rows,
          rowCount: res.row_count,
          elapsedMs: res.elapsed_ms,
          recommendedChart: chartType,
          chartXIndex,
          chartYIndex,
          truncated: res.truncated,
        },
        chartType,
      });
    } catch (e: unknown) {
      set({ running: false, error: String(e) });
    }
  },

  // Python 沙箱执行
  runPython: async () => {
    const { pythonText } = get();
    if (!pythonText.trim()) return;
    set({ running: true, error: null });
    try {
      const res = await ipc.dataRunPython(pythonText);
      if (!res.ok) {
        set({ running: false, error: res.error || '沙箱执行失败' });
        return;
      }
      set({
        running: false,
        result: res.row_count > 0 ? {
          columns: res.columns,
          rows: res.rows,
          rowCount: res.row_count,
          elapsedMs: Math.round(res.elapsed_s * 1000),
          recommendedChart: 'bar',
          chartXIndex: 0,
          chartYIndex: res.columns.length > 1 ? 1 : 0,
        } : get().result,
      });
    } catch (e: unknown) {
      set({ running: false, error: String(e) });
    }
  },

  setChartType: (t) => set({ chartType: t }),

  loadHistory: (id) => {
    // V1 简化：切换数据源（后续从后端加载任务详情）
    set({ selectedSourceId: id });
  },

  // 导出：真接后端
  doExport: async (fmt: string) => {
    const { result } = get();
    if (!result) return '⚠ 请先执行查询得到结果集';
    set({ exporting: true });
    try {
      const res = await ipc.dataExport(fmt, result.columns, result.rows);
      set({ exporting: false });
      return `✓ 导出成功：${res.path}\n🔒 水印: ${res.watermark}\n📝 MD5: ${res.md5} · ${res.row_count} 行`;
    } catch (e: unknown) {
      set({ exporting: false });
      return `❌ 导出失败：${String(e)}`;
    }
  },

  // 保存模板
  saveTemplate: async (name: string) => {
    try {
      await ipc.dataSaveTemplate(name);
      return true;
    } catch {
      return false;
    }
  },
}));
