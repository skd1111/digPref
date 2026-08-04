/**
 * routerStore —— Phase 2C V2 LLM 路由状态。
 * V0: 评分权重 / 熔断器 / 预算（纯前端 mock）
 * V2: 加 `setMetrics(metrics)` 由 RouterDashboard useEffect 拉真数据写入；
 *     加 `persistWeightsToBackend` 标志（ScoringWeightsEditor 保存时同步后端）。
 */
import { create } from 'zustand';
import type { ScoringWeights } from '@/ipc/invoke';

export type { ScoringWeights };
export const DEFAULT_WEIGHTS: ScoringWeights = {
  capability: 0.35,
  cost: 0.25,
  latency: 0.20,
  compliance: 0.15,
  availability: 0.05,
};

export interface CircuitInfo {
  name: string;
  state: 'closed' | 'open' | 'half_open';
}

export interface BudgetInfo {
  daily_spent: number;
  daily_limit: number;
}

export interface BackendLite {
  name: string;
  type: string;
  role: string;
  enabled: boolean;
}

interface RouterState {
  weights: ScoringWeights;
  setWeights: (w: Partial<ScoringWeights>) => void;
  resetWeights: () => void;

  circuits: CircuitInfo[];
  setCircuits: (c: CircuitInfo[]) => void;

  budget: BudgetInfo;
  setBudget: (b: BudgetInfo) => void;

  resetBreakerPending: string | null;
  setResetBreakerPending: (name: string | null) => void;

  // V2 增量：metrics 全集（RouterDashboard 5 秒轮询写入）
  setMetrics: (m: {
    circuits: Record<string, 'closed' | 'open' | 'half_open'>;
    budget: BudgetInfo;
    backends: BackendLite[];
  }) => void;

  // Phase 2C V0：模式切换
  runMode: 'auto' | 'manual';
  setRunMode: (m: 'auto' | 'manual') => void;

  sparkEnabled: boolean;
  setSparkEnabled: (b: boolean) => void;
}

export const useRouterStore = create<RouterState>((set) => ({
  weights: { ...DEFAULT_WEIGHTS },
  setWeights: (w) => set((s) => ({ weights: { ...s.weights, ...w } })),
  resetWeights: () => set({ weights: { ...DEFAULT_WEIGHTS } }),

  circuits: [],
  setCircuits: (c) => set({ circuits: c }),

  budget: { daily_spent: 0, daily_limit: 100 },
  setBudget: (b) => set({ budget: b }),

  resetBreakerPending: null,
  setResetBreakerPending: (name) => set({ resetBreakerPending: name }),

  // V2 增量：metrics 拉真数据写入（5 秒轮询）
  setMetrics: (m) =>
    set({
      circuits: Object.entries(m.circuits).map(([name, state]) => ({ name, state })),
      budget: m.budget,
      // 注意：backends 不进 store（与 ModelManagementPanel 独立维护）
    }),

  // V0 默认 auto + 不开 spark（V1 实际接推理后端）
  runMode: 'auto',
  setRunMode: (m) => set({ runMode: m }),

  // V2 增量：spark toggle 由 RouterDashboard 调 ipc.routerSetSparkMode()，
  // 这里只更新本地 UI 状态（避免双源真相）
  sparkEnabled: false,
  setSparkEnabled: (b) => set({ sparkEnabled: b }),
}));