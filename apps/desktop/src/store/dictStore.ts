/**
 * dictStore —— Phase 2H 数据字典状态（公共参数独立维护）。
 *
 * 数据源：后端 agent/datadict（dict.db），IPC 走 dict* wrapper。
 */
import { create } from "zustand";
import type { DictItem } from "@/types/datadict";
import { ipc } from "@/ipc/invoke";

function toItem(raw: Record<string, unknown>): DictItem {
  return raw as unknown as DictItem;
}

interface DictState {
  items: DictItem[];
  categories: string[];
  searchQuery: string;
  loading: boolean;
  error: string | null;

  loadItems: (category?: string) => Promise<void>;
  loadCategories: () => Promise<void>;
  search: (q: string) => Promise<void>;
  createItem: (body: {
    key: string;
    category: string;
    label: string;
    value: string;
    description?: string;
  }) => Promise<DictItem | null>;
  updateItem: (
    key: string,
    body: Record<string, unknown>,
  ) => Promise<DictItem | null>;
  deleteItem: (key: string) => Promise<boolean>;
  clearError: () => void;
}

export const useDictStore = create<DictState>((set, get) => ({
  items: [],
  categories: [],
  searchQuery: "",
  loading: false,
  error: null,

  loadItems: async (category) => {
    set({ loading: true, error: null });
    try {
      const r = await ipc.dictListItems(category);
      set({ items: (r.items ?? []).map(toItem), loading: false });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  },

  loadCategories: async () => {
    try {
      const r = await ipc.dictListCategories();
      set({ categories: r.categories ?? [] });
    } catch {
      // 分类拉取失败不阻塞列表
    }
  },

  search: async (q) => {
    const query = q.trim();
    set({ searchQuery: query, loading: true, error: null });
    try {
      if (!query) {
        const r = await ipc.dictListItems();
        set({ items: (r.items ?? []).map(toItem), loading: false });
        return;
      }
      const r = await ipc.dictSearchItems(query);
      set({ items: (r.items ?? []).map(toItem), loading: false });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  },

  createItem: async (body) => {
    try {
      const raw = await ipc.dictCreateItem(body);
      const item = toItem(raw);
      set({ items: [item, ...get().items], error: null });
      await get().loadCategories();
      return item;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  updateItem: async (key, body) => {
    try {
      const raw = await ipc.dictUpdateItem(key, body);
      const item = toItem(raw);
      set((s) => ({
        items: s.items.map((i) => (i.key === key ? item : i)),
        error: null,
      }));
      return item;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  deleteItem: async (key) => {
    try {
      await ipc.dictDeleteItem(key);
      set((s) => ({ items: s.items.filter((i) => i.key !== key) }));
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },

  clearError: () => set({ error: null }),
}));
