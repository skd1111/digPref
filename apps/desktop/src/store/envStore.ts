/**
 * envStore —— 当前活跃环境（dev/test/staging/prod）+ 环境列表全局状态。
 *
 * 设计要点：
 *   - 单一数据源：所有组件（顶部指示器、设置页、Agent invoke）都从这里读 active env
 *   - 持久化到 localStorage（key: eaide.activeEnv），下次启动自动恢复
 *   - 颜色映射：dev=绿 / test=蓝 / staging=橙 / prod=红，一眼能区分
 *   - 后端 /envconfig/list 与本地缓存双向同步
 */
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { invoke } from '@/ipc/invoke';

export type EnvName = 'dev' | 'test' | 'staging' | 'prod' | string;

export interface EnvMeta {
  environment: string;
  label: string;
  description: string;
  active: boolean;
  configured: boolean;
  updated_at: string;
}

/** 已知环境的标准配色（用于顶部醒目指示器）。未知环境回落到中性灰。 */
export const ENV_COLORS: Record<string, { fg: string; bg: string; dot: string; label: string }> = {
  dev:     { fg: '#0e3a2e', bg: '#059669', dot: '#059669', label: 'DEV'     },
  test:    { fg: '#0e2a4a', bg: '#0451a5', dot: '#0451a5', label: 'TEST'    },
  staging: { fg: '#4a2e0e', bg: '#b25c1a', dot: '#b25c1a', label: 'STAGING' },
  prod:    { fg: '#4a0e0e', bg: '#cd3131', dot: '#cd3131', label: 'PROD'    },
};

const NEUTRAL = { fg: '#1e1e1e', bg: '#6e6e6e', dot: '#6e6e6e', label: 'ENV' };

export function envColor(env: string | null | undefined): typeof NEUTRAL {
  if (!env) return NEUTRAL;
  return ENV_COLORS[env] ?? NEUTRAL;
}

interface EnvState {
  /** 活跃环境名（小写），null = 未设置 */
  activeEnv: string | null;
  /** 已知环境列表（来自后端 /envconfig/list） */
  list: EnvMeta[];
  loading: boolean;
  error: string | null;

  /** 从后端拉取环境列表 + 当前 active，自动同步 */
  refresh: () => Promise<void>;
  /** 切换 active 环境（写后端 + 写 localStorage） */
  setActive: (env: string) => Promise<void>;
  /** 仅本地切换（用于乐观更新，失败再回滚） */
  setActiveLocal: (env: string | null) => void;
}

export const useEnvStore = create<EnvState>()(
  persist(
    (set, get) => ({
      activeEnv: null,
      list: [],
      loading: false,
      error: null,

      refresh: async () => {
        set({ loading: true, error: null });
        // ★ 自动重试：应对"首次打开"时 Python Agent 还没完全就绪的竞态
        // 重试 4 次，间隔 0 / 800 / 1500 / 3000 ms（递增 backoff）
        const delays = [0, 800, 1500, 3000];
        let lastErr: unknown = null;
        for (const d of delays) {
          if (d > 0) await new Promise((r) => setTimeout(r, d));
          try {
            const r = await invoke<{ active: string | null; environments: EnvMeta[] }>(
              'envconfig_list',
            );
            const persisted = get().activeEnv;
            const backendActive = r.active;
            const activeEnv = persisted ?? backendActive ?? null;
            set({ list: r.environments, activeEnv, loading: false, error: null });
            return; // 成功直接返回
          } catch (e) {
            lastErr = e;
            // 继续重试
          }
        }
        set({ error: String(lastErr), loading: false });
      },

      setActive: async (env: string) => {
        const prev = get().activeEnv;
        // 乐观更新（前端先变，后端跟上）
        set({ activeEnv: env });
        try {
          await invoke('envconfig_activate', { env });
          // 激活成功后**强制 refresh** 拉取最新 list + backend active，
          // 保证 envStore 与 Settings / SidePanel 完全一致
          await get().refresh();
        } catch (e) {
          // 回滚 + 提示
          set({ activeEnv: prev, error: String(e) });
          throw e;
        }
      },

      setActiveLocal: (env) => set({ activeEnv: env }),
    }),
    {
      name: 'eaide.activeEnv',
      storage: createJSONStorage(() => localStorage),
      // 只持久化 activeEnv，不持久化 list（每次启动重新拉）
      partialize: (s) => ({ activeEnv: s.activeEnv }),
    },
  ),
);
