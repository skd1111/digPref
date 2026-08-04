/**
 * sessionsStore —— Phase 6 V0 + V1.5 会话管理状态。
 *
 * V1.5 (2026-07-31) 扩展：
 *   - 新字段：is_branch / parent_session_id / branch_label / share_tokens / permissions /
 *     stats / event_chain / recovery_report
 *   - 新 actions：search / branch_create / branches_list / share_* (4) /
 *     export / import / recovery / event_chain / event_chain_verify /
 *     append_message / record_checkpoint / stats
 *
 * V1.5 仍然不持久化（刷新后默认 activeSessionId=null，重新拉列表）。
 * 与 envconfig / biznav 同风格（不 zustand.persist）。
 */
import { create } from 'zustand';
import { ipc } from '@/ipc/invoke';
import { listen, EVT } from '@/ipc/events';

// ---- 类型（与 Python sessions.models + invoke.ts 镜像）--------------------------

interface Session {
  id: string;
  title: string;
  owner: string;
  project_name: string;
  status: string;
  created_at: number;
  updated_at: number;
  thread_id: string;
  metadata: Record<string, unknown>;
  // V1.5
  parent_session_id: string | null;
  branch_from_checkpoint_id: string | null;
  branch_label: string;
  share_tokens: Array<Record<string, unknown>>;
  permissions: Record<string, string>;
  shared_at: number;
}

interface SessionDetail extends Session {
  messages: Array<Record<string, unknown>>;
  checkpoints: Array<Record<string, unknown>>;
}

interface KBSearchResult {
  backend: string;
  elapsed_ms: number;
  results: Array<Record<string, unknown>>;
  snippet: string;
}

// V1.5 新类型
interface SessionStats {
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
}

interface SearchHit {
  session_id: string;
  created_at: number;
  title: string;
  content_snippet: string;
  tool_name: string;
  tool_result: string;
  relevance: number;
}

interface ShareTokenInfo {
  token: string;
  permission: 'read' | 'write';
  created_at: number;
  expires_at: number | null;
}

interface BranchInfo {
  id: string;
  title: string;
  parent_session_id: string | null;
  branch_from_checkpoint_id: string | null;
  branch_label: string;
  created_at: number;
  updated_at: number;
  status: string;
}

interface EventChainEntry {
  id: number;
  session_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  prev_hash: string;
  hash: string;
  actor: string;
  created_at: number;
}

interface RecoveryReport {
  total: number;
  resumable_ids: string[];
  oldest_idle_ms: number;
  generated_at: number;
  threshold_ms: number;
  needs_recovery: boolean;
}

// ---- Store 接口 --------------------------------------------------------------

interface SessionsState {
  // 基础
  sessions: Session[];
  activeSessionId: string | null;
  activeSessionDetail: SessionDetail | null;
  loading: boolean;
  error: string | null;
  kbSnippet: string | null;
  // V1.5 扩展
  stats: Record<string, SessionStats>;
  searchResults: SearchHit[];
  searchQuery: string;
  branches: Record<string, BranchInfo[]>;
  shareInfo: Record<string, { tokens: ShareTokenInfo[]; permissions: Record<string, string> }>;
  eventChains: Record<string, EventChainEntry[]>;
  recoveryReport: RecoveryReport | null;
  lastCompressionEvent: Record<string, unknown> | null;
  lastMemoryConsolidated: Record<string, unknown> | null;

  // V0 actions
  loadList: (project_name?: string) => Promise<void>;
  create: (title: string, opts?: { owner?: string; project_name?: string }) => Promise<Session | null>;
  get: (session_id: string) => Promise<SessionDetail | null>;
  remove: (session_id: string) => Promise<boolean>;
  kbSearch: (query: string, top_k?: number) => Promise<KBSearchResult | null>;
  setActive: (session_id: string | null) => void;
  // V1.5 actions
  loadStats: (session_id: string) => Promise<SessionStats | null>;
  search: (query: string, opts?: { project_name?: string; limit?: number }) => Promise<void>;
  clearSearch: () => void;
  appendMessage: (
    session_id: string,
    body: {
      role?: string;
      content?: string;
      tool_name?: string;
      tool_args?: Record<string, unknown>;
      tool_result?: string;
      actor?: string;
    },
  ) => Promise<boolean>;
  recordCheckpoint: (
    session_id: string,
    body: { thread_id: string; checkpoint_id: string; label?: string; description?: string },
  ) => Promise<boolean>;
  branchCreate: (
    session_id: string,
    body: { branch_label: string; from_checkpoint_id?: string; title_suffix?: string; actor?: string },
  ) => Promise<BranchInfo | null>;
  branchesList: (session_id: string) => Promise<BranchInfo[]>;
  shareCreate: (
    session_id: string,
    body: { permission?: 'read' | 'write'; expires_in_ms?: number; actor?: string },
  ) => Promise<ShareTokenInfo | null>;
  shareRevoke: (session_id: string, token: string, actor?: string) => Promise<boolean>;
  shareGrant: (
    session_id: string,
    body: { target_actor: string; permission: 'read' | 'write'; granter?: string },
  ) => Promise<boolean>;
  shareList: (session_id: string, actor?: string) => Promise<{ tokens: ShareTokenInfo[]; permissions: Record<string, string> }>;
  exportSession: (
    session_id: string,
    body: { output_path: string; actor?: string; include_messages?: boolean; include_event_chain?: boolean; scrub_pii?: boolean },
  ) => Promise<{ path: string; bytes: number; checksum: string; exported_at: number } | null>;
  importSession: (
    body: { eas_path: string; actor?: string; import_as_branch?: boolean; parent_session_id?: string | null },
  ) => Promise<{ new_session_id: string } | null>;
  loadRecovery: (opts?: { idle_threshold_ms?: number; limit?: number }) => Promise<RecoveryReport | null>;
  loadEventChain: (session_id: string, limit?: number) => Promise<EventChainEntry[]>;
  verifyEventChain: (session_id: string) => Promise<{ valid: boolean; broken_reason: string | null }>;
  /** 订阅 SSE 事件：compression_applied + memory_consolidated */
  subscribeSSE: () => () => void;
}

// ---- Implementation ---------------------------------------------------------

export const useSessionsStore = create<SessionsState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  activeSessionDetail: null,
  loading: false,
  error: null,
  kbSnippet: null,
  stats: {},
  searchResults: [],
  searchQuery: '',
  branches: {},
  shareInfo: {},
  eventChains: {},
  recoveryReport: null,
  lastCompressionEvent: null,
  lastMemoryConsolidated: null,

  // V0 actions
  loadList: async (project_name?: string) => {
    set({ loading: true, error: null });
    try {
      const opts = project_name ? { project_name } : {};
      const list = await ipc.sessionsList(opts);
      set({ sessions: list ?? [], loading: false });
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  create: async (title, opts) => {
    set({ loading: true, error: null });
    try {
      const body = {
        title,
        owner: opts?.owner ?? 'default',
        project_name: opts?.project_name ?? 'default',
      };
      const s = await ipc.sessionsCreate(body);
      set((state) => ({
        sessions: [s, ...state.sessions],
        loading: false,
        activeSessionId: s.id,
      }));
      return s;
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  get: async (session_id) => {
    set({ loading: true, error: null });
    try {
      const detail = await ipc.sessionsGet(session_id);
      set({
        loading: false,
        activeSessionId: session_id,
        activeSessionDetail: detail,
      });
      return detail;
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  remove: async (session_id) => {
    set({ loading: true, error: null });
    try {
      await ipc.sessionsDelete(session_id);
      set((state) => ({
        sessions: state.sessions.filter((s) => s.id !== session_id),
        loading: false,
        activeSessionId: state.activeSessionId === session_id ? null : state.activeSessionId,
        activeSessionDetail:
          state.activeSessionId === session_id ? null : state.activeSessionDetail,
      }));
      return true;
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },

  kbSearch: async (query, top_k = 3) => {
    set({ loading: true, error: null });
    try {
      const result = await ipc.sessionsKbSearch({ query, top_k });
      set({ loading: false, kbSnippet: result?.snippet ?? null });
      return result;
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  setActive: (session_id) => set({ activeSessionId: session_id }),

  // V1.5 actions
  loadStats: async (session_id) => {
    try {
      const stats = await ipc.sessionsStats(session_id);
      set((state) => ({ stats: { ...state.stats, [session_id]: stats } }));
      return stats;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  search: async (query, opts) => {
    set({ loading: true, error: null, searchQuery: query });
    try {
      const result = await ipc.sessionsSearch({
        query,
        ...(opts?.project_name !== undefined && { project_name: opts.project_name }),
        ...(opts?.limit !== undefined && { limit: opts.limit }),
      });
      set({ loading: false, searchResults: result?.hits ?? [] });
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  clearSearch: () => set({ searchResults: [], searchQuery: '' }),

  appendMessage: async (session_id, body) => {
    try {
      await ipc.sessionsAppendMessage(session_id, { ...body, actor: body.actor ?? 'default' });
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },

  recordCheckpoint: async (session_id, body) => {
    try {
      await ipc.sessionsRecordCheckpoint(session_id, body);
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },

  branchCreate: async (session_id, body) => {
    try {
      const branch = await ipc.sessionsBranchCreate(session_id, {
        ...body,
        actor: body.actor ?? 'default',
      });
      // 刷新列表
      void get().loadList();
      return branch;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  branchesList: async (session_id) => {
    try {
      const result = await ipc.sessionsBranchesList(session_id);
      const list = result?.branches ?? [];
      set((state) => ({ branches: { ...state.branches, [session_id]: list } }));
      return list;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return [];
    }
  },

  shareCreate: async (session_id, body) => {
    try {
      const token = await ipc.sessionsShareCreate(session_id, {
        ...body,
        actor: body.actor ?? 'default',
      });
      return token;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  shareRevoke: async (session_id, token, actor = 'default') => {
    try {
      await ipc.sessionsShareRevoke(session_id, token, actor);
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },

  shareGrant: async (session_id, body) => {
    try {
      await ipc.sessionsShareGrant(session_id, {
        ...body,
        granter: body.granter ?? 'default',
      });
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },

  shareList: async (session_id, actor = 'default') => {
    try {
      const info = await ipc.sessionsShareList(session_id, actor);
      set((state) => ({
        shareInfo: {
          ...state.shareInfo,
          [session_id]: {
            tokens: info.share_tokens as unknown as ShareTokenInfo[],
            permissions: info.permissions,
          },
        },
      }));
      return {
        tokens: info.share_tokens as unknown as ShareTokenInfo[],
        permissions: info.permissions,
      };
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return { tokens: [], permissions: {} };
    }
  },

  exportSession: async (session_id, body) => {
    try {
      const result = await ipc.sessionsExport(session_id, {
        ...body,
        actor: body.actor ?? 'default',
      });
      return result;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  importSession: async (body) => {
    try {
      const result = await ipc.sessionsImport({
        ...body,
        actor: body.actor ?? 'default',
      });
      void get().loadList();
      return result;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  loadRecovery: async (opts) => {
    try {
      const report = await ipc.sessionsRecovery(opts ?? {});
      set({ recoveryReport: report });
      return report;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  loadEventChain: async (session_id, limit) => {
    try {
      const result = await ipc.sessionsEventChain(session_id, limit);
      const entries = result?.entries ?? [];
      set((state) => ({ eventChains: { ...state.eventChains, [session_id]: entries } }));
      return entries;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return [];
    }
  },

  verifyEventChain: async (session_id) => {
    try {
      const result = await ipc.sessionsEventChainVerify(session_id);
      return { valid: result.valid, broken_reason: result.broken_reason };
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return { valid: false, broken_reason: String(e) };
    }
  },

  subscribeSSE: () => {
    const unlistenFns: Array<() => void> = [];
    let mounted = true;
    void listen(EVT.SESSION_COMPRESSION_APPLIED, ({ payload }) => {
      if (mounted) set({ lastCompressionEvent: payload as Record<string, unknown> });
    }).then((u) => mounted && unlistenFns.push(u));
    void listen(EVT.SESSION_MEMORY_CONSOLIDATED, ({ payload }) => {
      if (mounted) set({ lastMemoryConsolidated: payload as Record<string, unknown> });
    }).then((u) => mounted && unlistenFns.push(u));
    return () => {
      mounted = false;
      unlistenFns.forEach((u) => u());
    };
  },
}));

// ---- Selectors ---------------------------------------------------------------

export const selectActiveSession = (s: SessionsState): Session | null =>
  s.sessions.find((x) => x.id === s.activeSessionId) ?? null;

export const selectActiveStats = (s: SessionsState): SessionStats | null =>
  s.activeSessionId ? s.stats[s.activeSessionId] ?? null : null;

export const selectBranchesFor = (sessionId: string) => (s: SessionsState): BranchInfo[] =>
  s.branches[sessionId] ?? [];

export const selectShareInfo = (sessionId: string) => (s: SessionsState) =>
  s.shareInfo[sessionId] ?? { tokens: [], permissions: {} };

export const selectEventChain = (sessionId: string) => (s: SessionsState): EventChainEntry[] =>
  s.eventChains[sessionId] ?? [];