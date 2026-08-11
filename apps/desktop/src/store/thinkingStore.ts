/**
 * thinkingStore —— Phase 16 思维链数据流。
 *
 * 数据来源：Python Agent `/trace/session/{runId}`（经 Rust trace_get_session 桥接）。
 * 触发时机：
 *   - 每次启动面板为空，不自动加载历史会话
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
