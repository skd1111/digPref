/**
 * assetStore —— 系统资产缓存（数据库、REST API、SSH 主机、RPA 目标）。
 *
 * 数据来源：应用数据目录下的 systems.yaml（Windows: %APPDATA%\eaide\systems.yaml，
 * 可用 EAIDE_SYSTEMS_PATH 覆盖），通过 Rust asset CRUD 命令持久化。
 * V1 升级：addAsset/updateAsset/removeAsset 真接 Rust → systems.yaml 持久化。
 */
import { create } from 'zustand';
import { ipc } from '@/ipc/invoke';

export interface AssetNode {
  id: string;
  type: 'database' | 'rest' | 'ssh' | 'rpa';
  label: string;
  icon: string;
  meta: Record<string, unknown>;
}

interface AssetState {
  tree: AssetNode[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  removeAsset: (id: string) => Promise<void>;
  addAsset: (node: Omit<AssetNode, 'id'> & { id?: string }) => Promise<void>;
  updateAsset: (id: string, patch: Partial<Pick<AssetNode, 'label' | 'icon' | 'meta'>>) => Promise<void>;
}

export const useAssetStore = create<AssetState>((set) => ({
  tree: [],
  loading: false,
  error: null,

  removeAsset: async (id) => {
    try {
      await ipc.removeAsset(id);
      set((s) => ({ tree: s.tree.filter((n) => n.id !== id) }));
    } catch (e) {
      set({ error: String(e) });
    }
  },

  addAsset: async (node) => {
    try {
      const result = await ipc.addAsset(node as Record<string, unknown>);
      const validated = normalizeAsset(result);
      set((s) => ({ tree: [...s.tree, validated] }));
    } catch (e) {
      set({ error: String(e) });
    }
  },

  updateAsset: async (id, patch) => {
    try {
      const result = await ipc.updateAsset(id, patch as Record<string, unknown>);
      const validated = normalizeAsset(result);
      set((s) => ({
        tree: s.tree.map((n) => (n.id === id ? validated : n)),
      }));
    } catch (e) {
      set({ error: String(e) });
    }
  },

  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const data = await ipc.listAssets();
      const validated = (Array.isArray(data) ? data : []).map(normalizeAsset);
      set({ tree: validated, loading: false, error: null });
    } catch (e) {
      set({ loading: false, error: String(e) });
    }
  },
}));

function normalizeAsset(item: unknown): AssetNode {
  const obj = (item ?? {}) as Record<string, unknown>;
  return {
    id: String(obj.id ?? ''),
    type: (['database', 'rest', 'ssh', 'rpa'].includes(obj.type as string) ? obj.type : 'database') as AssetNode['type'],
    label: String(obj.label ?? obj.id ?? ''),
    icon: String(obj.icon ?? 'server'),
    meta: (obj.meta ?? {}) as Record<string, unknown>,
  };
}
