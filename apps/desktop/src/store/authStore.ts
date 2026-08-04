/**
 * authStore —— 最小化的认证/会话状态。
 *
 * 本地 IDE shell 使用操作系统用户身份；生产部署可对接 SSO/IdP。
 * 初始化时尝试通过 Tauri OS 插件获取当前用户名。
 */
import { create } from 'zustand';

interface AuthState {
  operator: string | null;
  setOperator: (name: string | null) => void;
  /** 从操作系统获取当前用户名并设置 */
  initOperator: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  operator: null,
  setOperator: (name) => set({ operator: name }),
  initOperator: async () => {
    try {
      // 尝试通过 Tauri OS 插件获取用户名
      const { invoke } = await import('@tauri-apps/api/core');
      const name = await invoke<string>('credential_service_name');
      if (name) set({ operator: name });
    } catch {
      // 开发环境或权限不足时使用 fallback
      set({ operator: 'local-dev' });
    }
  },
}));
