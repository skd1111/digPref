/**
 * traceStore —— 控制台 (右侧) 的两条数据流。
 *
 *   - steps: 执行链路 SSE (TraceStep) —— LangGraph 各节点状态
 *   - consoleEntries: AI 解释 / agent 日志 (from agent://log + 主动 log)
 *
 * Phase 12 V1：两条流合并渲染到「控制台」面板。
 */
import { create } from 'zustand';
import type { TraceStep } from '@eaide/shared-protocol';
import { isMockSource, isMockText } from '@/lib/mockFilter';

export type ConsoleCategory =
  | 'codenav.explain'
  | 'codenav.jump'
  | 'intent'
  | 'plan'
  | 'repair'
  | 'tool_call'
  | 'log'
  | string;

export interface ConsoleEntry {
  id: string;
  category: ConsoleCategory;
  /** 入口展示文本（codenav.explain: 「**LMRouter** — ...」） */
  text: string;
  /** LLM 解释完整正文（仅 codenav.explain 用，前端可展开看全） */
  fullText?: string;
  source?: 'llm' | 'mock' | 'log' | 'trace';
  status?: 'running' | 'ok' | 'err';
  latencyMs?: number;
  symbol?: string;
  backend?: string | null;
  confidence?: number;
  ts: number;
}

interface TraceState {
  steps: TraceStep[];
  append: (s: TraceStep) => void;
  reset: () => void;

  /** 执行块 → 思维链跳转高亮查询（BUGFIX #153）：工具名或节点名；
   *  ts 保证重复点同一条也能再次触发闪烁；
   *  occurrence（2026-08-27 树形合并）：同名工具多次调用时定位第 N 次（1 基，
   *  从早到晚），缺省回退最新一条（旧行为兼容） */
  highlight: { query: string; ts: number; occurrence?: number } | null;
  setHighlight: (query: string, occurrence?: number) => void;

  consoleEntries: ConsoleEntry[];
  /** 推一条新 entry，返回生成的 id（让 caller 用 updateConsole 原地更新同一行 —— 流式体验） */
  pushConsole: (e: Omit<ConsoleEntry, 'id' | 'ts'>) => string;
  /** 原地更新已存在的 entry（按 id 匹配） */
  updateConsole: (id: string, patch: Partial<Omit<ConsoleEntry, 'id' | 'ts'>>) => void;
  clearConsole: () => void;
}

const newConsoleId = (): string =>
  `c-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

export const useTraceStore = create<TraceState>((set) => ({
  steps: [],
  append: (s) => set((state) => ({ steps: [...state.steps, s] })),
  reset: () => set({ steps: [] }),

  highlight: null,
  setHighlight: (query, occurrence) =>
    set({
      highlight: occurrence != null ? { query, ts: Date.now(), occurrence } : { query, ts: Date.now() },
    }),

  consoleEntries: [],
  pushConsole: (e) => {
    const id = newConsoleId();
    // mock 数据不进入控制台（含 running 占位行）
    if (isMockSource(e.source) || isMockText(e.text)) {
      return id;
    }
    set((state) => ({
      consoleEntries: [...state.consoleEntries, { ...e, id, ts: Date.now() }],
    }));
    return id;
  },
  updateConsole: (id, patch) =>
    set((state) => {
      const next = state.consoleEntries.map((e) =>
        e.id === id ? { ...e, ...patch } : e,
      );
      // 更新后变成 mock（source=mock 或文本带标记）→ 从控制台移除
      return {
        consoleEntries: next.filter(
          (e) => !isMockSource(e.source) && !isMockText(e.text),
        ),
      };
    }),
  clearConsole: () => set({ consoleEntries: [] }),
}));
