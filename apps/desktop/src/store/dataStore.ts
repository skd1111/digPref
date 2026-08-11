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
import { tableFromIPC, type Table } from 'apache-arrow';
import { ipc } from '@/ipc/invoke';
import { EVT, listen } from '@/ipc/events';

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
  querySql: string;
  rowCount: number;
  createdAt: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface QueryResult {
  columns: string[];
  rows: Array<Array<string | number>>;
  /** 大结果集列存形态（Arrow 流解析后；与 rows 二选一，严禁整表进 React state） */
  columnar?: Record<string, Array<string | number>>;
  rowCount: number;
  elapsedMs: number;
  recommendedChart: ChartType;
  chartXIndex: number;
  chartYIndex: number;
  truncated?: boolean;
  /** 关联的后端 analysis_tasks id（导出服务端取数用） */
  taskId?: string;
}

/** 取某行某列单元格（兼容内联 rows 与列存 columnar 两种形态） */
export function cellFor(result: QueryResult, row: number, col: number): string | number {
  if (result.columnar) {
    const arr = result.columnar[result.columns[col]];
    return arr?.[row] ?? '';
  }
  return result.rows[row]?.[col] ?? '';
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
  streaming: boolean;
  result: QueryResult | null;
  chartType: ChartType;
  exporting: boolean;
  /** HITL 重查询待确认（后端 needs_confirm，缺口 3） */
  pendingConfirm: { sql: string; message: string } | null;
  lastTaskId: string;

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
  confirmRun: () => Promise<void>;
  cancelConfirm: () => void;
  runPython: () => Promise<void>;
  setChartType: (t: ChartType) => void;
  fetchHistory: () => Promise<void>;
  loadHistory: (id: string) => void;
  doExport: (fmt: string) => Promise<string | null>;
  saveTemplate: (name: string) => Promise<boolean>;
}

/** 判断是否只读 SELECT（前端提示用；后端 guard 硬拦截） */
export function isReadOnlySql(sql: string): boolean {
  const s = sql.replace(/--.*$/gm, '').toLowerCase();
  return !/\b(update|delete|drop|truncate|insert|alter|grant|revoke|create|replace|merge)\b/.test(s);
}

// ---- 大结果集 Arrow 流工具（缺口 5） ------------------------------------

interface StreamChunkPayload {
  task_id: string;
  seq: number;
  kind: 'batch' | 'meta';
  data_base64?: string;
  text?: string;
}

/** base64 → Uint8Array */
export function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/** Arrow Table → 列存数组（BigInt 转 number） */
function tableToColumnar(
  table: Table,
  acc: Record<string, Array<string | number>>,
): void {
  for (const field of table.schema.fields) {
    const vec = table.getChild(field.name);
    const arr = acc[field.name] ?? (acc[field.name] = []);
    if (!vec) continue;
    for (const v of vec) {
      if (v === null || v === undefined) arr.push('');
      else if (typeof v === 'bigint') arr.push(Number(v));
      else if (typeof v === 'number' || typeof v === 'string') arr.push(v);
      else arr.push(String(v));
    }
  }
}

/**
 * SQL 执行内部实现（runQuery / confirmRun 共用）。
 * 流程：
 *   1. needs_confirm → 弹 HITL 确认（pendingConfirm），不执行
 *   2. stream_ref 非空 → 订阅 Arrow 流事件 + ipc.dataStreamResult 中继
 *   3. 内联 rows → 直接入 store
 */
async function runSqlInternal(confirmed: boolean): Promise<void> {
  const st = useDataStore.getState();
  const { sqlText, selectedSourceId } = st;
  if (!sqlText.trim()) return;
  useDataStore.setState({ running: true, error: null });
  try {
    const res = await ipc.dataRunSql(sqlText, selectedSourceId || undefined, confirmed);

    // HITL：重查询需用户确认（缺口 3）
    if (res.needs_confirm) {
      useDataStore.setState({
        running: false,
        pendingConfirm: {
          sql: res.sql || sqlText,
          message: res.message || '检测到多表 JOIN / 全表扫描，请确认后执行',
        },
      });
      return;
    }

    if (!res.ok) {
      useDataStore.setState({ running: false, error: res.error || '执行失败' });
      return;
    }

    // 图表推荐（失败不影响主流程）
    let chartType: ChartType = 'bar';
    let chartXIndex = 0;
    let chartYIndex = res.columns.length > 1 ? 1 : 0;
    try {
      const reco = await ipc.dataChartRecommend(res.columns, res.dtypes, res.row_count);
      chartType = (reco.chart_type as ChartType) || 'bar';
      chartXIndex = reco.x_index;
      chartYIndex = reco.y_index;
    } catch { /* 推荐失败不影响主流程 */ }

    const taskId = res.task_id ?? '';

    // 大结果集：走 WS + Arrow 流（整表不进 React state，缺口 5）
    if (res.stream_ref) {
      useDataStore.setState({ running: false, streaming: true, lastTaskId: taskId });
      const buffers: Uint8Array[] = [];
      const unChunk = await listen<StreamChunkPayload>(EVT.DATA_STREAM_CHUNK, (ev) => {
        const p = ev.payload;
        if (p.task_id !== taskId || p.kind !== 'batch' || !p.data_base64) return;
        buffers.push(base64ToBytes(p.data_base64));
      });
      try {
        await ipc.dataStreamResult(taskId);
        // 等 done 事件落地（emit 异步，最多等 2s 兑底）
        await new Promise<void>((resolve) => {
          let settled = false;
          const timer = setTimeout(() => {
            if (!settled) { settled = true; resolve(); }
          }, 2000);
          void listen<{ task_id: string }>(EVT.DATA_STREAM_DONE, (ev) => {
            if (ev.payload.task_id !== taskId || settled) return;
            settled = true;
            clearTimeout(timer);
            resolve();
          }).then((un) => setTimeout(un, 2100));
        });
      } finally {
        unChunk();
      }
      // 逐批解析（每帧是独立完整 IPC stream）后合并列存
      const columnar: Record<string, Array<string | number>> = {};
      for (const buf of buffers) {
        try {
          tableToColumnar(tableFromIPC(buf), columnar);
        } catch { /* 单批解析失败跳过，不阻断整体 */ }
      }
      useDataStore.setState({
        streaming: false,
        result: {
          columns: res.columns,
          rows: [],
          columnar,
          rowCount: res.row_count,
          elapsedMs: res.elapsed_ms,
          recommendedChart: chartType,
          chartXIndex,
          chartYIndex,
          truncated: res.truncated,
          taskId,
        },
        chartType,
      });
      return;
    }

    // 内联小结果
    useDataStore.setState({
      running: false,
      lastTaskId: taskId,
      result: {
        columns: res.columns,
        rows: res.rows,
        rowCount: res.row_count,
        elapsedMs: res.elapsed_ms,
        recommendedChart: chartType,
        chartXIndex,
        chartYIndex,
        truncated: res.truncated,
        taskId,
      },
      chartType,
    });
  } catch (e: unknown) {
    useDataStore.setState({ running: false, streaming: false, error: String(e) });
  }
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
  streaming: false,
  result: null,
  chartType: 'bar',
  exporting: false,
  pendingConfirm: null,
  lastTaskId: '',

  // 从后端加载数据源列表
  fetchSources: async () => {
    set({ loading: true, error: null });
    try {
      const resp = await ipc.dataListSources();
      // 后端 GET /data/sources 返回 { sources: [...], count } 对象（非数组），
      // 直接 .map 会抛 "(intermediate value).map is not a function" —— 这里解包兼容两种形态
      type RawSource = {
        id: string;
        name: string;
        type: string;
        schema_cache?: Array<{
          name: string;
          comment?: string;
          columns?: Array<{ name: string; dtype?: string; comment?: string }>;
        }>;
      };
      const raw: RawSource[] = Array.isArray(resp)
        ? (resp as unknown as RawSource[])
        : ((resp as unknown as { sources?: RawSource[] })?.sources ?? []);
      const sources: DataSource[] = raw.map((s) => ({
        id: s.id,
        name: s.name,
        type: (s.type as SourceType) || 'mysql',
        status: 'connected' as const,
        tables: (Array.isArray(s.schema_cache) ? s.schema_cache : []).map((t) => ({
          name: t.name,
          comment: t.comment || '',
          columns: (Array.isArray(t.columns) ? t.columns : []).map((c) => ({
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
      // 白名单拒绝（缺口 10）：生成 SQL 含非查询语句 → 不下发
      if (!res.sql) {
        set((s) => ({
          running: false,
          chat: [...s.chat, { role: 'assistant', content: `❌ ${(res as { error?: string }).error || 'NL2SQL 未生成有效查询'}` }],
        }));
        return;
      }
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

  // SQL 执行：真接后端（HITL 确认流 + 大结果集 Arrow 流，缺口 3/5）
  runQuery: async () => {
    await runSqlInternal(false);
  },

  // HITL：用户确认重查询后以 confirmed=true 重提
  confirmRun: async () => {
    const pc = get().pendingConfirm;
    if (!pc) return;
    set({ pendingConfirm: null });
    await runSqlInternal(true);
  },

  cancelConfirm: () => set({ pendingConfirm: null }),

  // Python 沙箱执行（关联上一步 SQL 结果 task_id，缺口 8）
  runPython: async () => {
    const { pythonText, lastTaskId } = get();
    if (!pythonText.trim()) return;
    set({ running: true, error: null });
    try {
      const res = await ipc.dataRunPython(pythonText, lastTaskId || undefined);
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

  // 历史分析：拉后端 /data/tasks（缺口 9）
  fetchHistory: async () => {
    try {
      const resp = await ipc.dataListTasks(50);
      const tasks = Array.isArray(resp?.tasks) ? resp.tasks : [];
      set({
        history: tasks.map((t) => ({
          id: t.id,
          name: t.name || (t.query_sql || '').slice(0, 40),
          querySql: t.query_sql || '',
          rowCount: t.result_metadata?.row_count ?? 0,
          createdAt: t.created_at
            ? new Date(t.created_at * 1000).toLocaleString()
            : '',
        })),
      });
    } catch (e: unknown) {
      set({ error: String(e) });
    }
  },

  // 点击历史项：回填 SQL 到编辑器可重跑
  loadHistory: (id) => {
    const task = get().history.find((h) => h.id === id);
    if (task) {
      set({ sqlText: task.querySql, editorMode: 'sql', lastTaskId: task.id });
    }
  },

  // 导出：task_id 优先（服务端取数，整表不经前端，缺口 5）
  doExport: async (fmt: string) => {
    const { result, lastTaskId } = get();
    if (!result) return '⚠ 请先执行查询得到结果集';
    set({ exporting: true });
    try {
      const res = await ipc.dataExport(
        fmt,
        result.columns,
        result.columnar ? [] : result.rows,
        '数据报表',
        result.taskId || lastTaskId || undefined,
      );
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
