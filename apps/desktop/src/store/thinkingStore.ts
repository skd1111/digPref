/**
 * thinkingStore —— Phase 16 思维链数据流。
 *
 * 数据来源：Python Agent `/trace/session/{runId}`（经 Rust trace_get_session 桥接）。
 * 触发时机：
 *   - 面板挂载时 → loadLatestSession() 自动加载最近会话的思维链
 *   - SSE trace 事件携带 runId → 绑定实时会话 + 防抖刷新；done 事件 → 最终刷新
 *
 * 模式隔离：store 不判断模式 —— 后端一律记录，渲染隔离由组件层完成
 * （仅开发 mode='full' 挂载 ThinkingChainPanel）。
 */
import { create } from 'zustand';
import type { ThinkingStep } from '@eaide/shared-protocol';
import { ipc } from '@/ipc/invoke';

interface ThinkingState {
  /** 当前会话（= Agent run_id） */
  sessionId: string | null;
  steps: ThinkingStep[];
  loading: boolean;
  /** 最近一次刷新失败原因（Agent 未就绪等，UI 可静默） */
  error: string | null;

  setSessionId: (id: string | null) => void;
  /** 启动/挂载时自动加载最近一个会话的思维链（Agent 未就绪时静默重试） */
  loadLatestSession: () => Promise<void>;
  refresh: (sessionId?: string) => Promise<void>;
  reset: () => void;
}

export const useThinkingStore = create<ThinkingState>((set, get) => ({
  sessionId: null,
  steps: [],
  loading: false,
  error: null,

  setSessionId: (id) => {
    const cur = get().sessionId;
    if (id === cur) return;
    // 会话切换 → 清空旧步骤再拉新
    set({ sessionId: id, steps: [], error: null });
    if (id) void get().refresh(id);
  },

  loadLatestSession: async () => {
    // 已有会话（实时 SSE 已绑定或已加载过）→ 不覆盖
    if (get().sessionId) return;
    try {
      const resp = await ipc.traceRecentSessions();
      const latest = resp.sessions?.[0];
      // 竞态保护：返回时若实时 SSE 已绑定会话 → 让位
      if (!latest || get().sessionId) return;
      get().setSessionId(latest.session_id);
    } catch {
      // Agent 未就绪 → 静默降级（调用方可重试）
    }
  },

  refresh: async (sessionId) => {
    const sid = sessionId ?? get().sessionId;
    if (!sid) return;
    set({ loading: true });
    try {
      const resp = await ipc.traceGetSession(sid);
      // 竞态保护：刷新返回时会话可能已切换
      if (get().sessionId !== sid) return;
      set({ steps: resp.steps ?? [], loading: false, error: null });
    } catch (e) {
      // Agent 未就绪 / 网络错误 → 静默降级（不打扰用户）
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  reset: () => set({ sessionId: null, steps: [], loading: false, error: null }),
}));
