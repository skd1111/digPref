/**
 * opsNavStore —— Phase 2H 运营工作台「业务列表」导航状态。
 *
 * - selectedItemId：当前选中的业务功能点（静态导航项 或 工程提炼功能点）
 * - skillBindings：业务功能点 id → Skill id（持久化；功能点=经验总结，以 Skill 存在）
 *
 * 会话注入走 chatStore.opsNavContext（由 OperationsWorkbench 写入），
 * 本 store 只维护导航选中态 + 绑定关系，避免双向耦合。
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface OpsNavState {
  /** 当前选中的业务功能点 id */
  selectedItemId: string | null;
  /** 业务功能点 id → Skill id（公共部分在数据字典，业务经验在 Skill） */
  skillBindings: Record<string, string>;
  /** 左侧导航搜索关键字 */
  searchQuery: string;
  /** 新建功能点对话框是否打开 */
  createDialogOpen: boolean;

  selectItem: (itemId: string | null) => void;
  bindSkill: (itemId: string, skillId: string) => void;
  unbindSkill: (itemId: string) => void;
  setSearchQuery: (q: string) => void;
  openCreateDialog: () => void;
  closeCreateDialog: () => void;
}

export const useOpsNavStore = create<OpsNavState>()(
  persist(
    (set) => ({
      selectedItemId: null,
      skillBindings: {},
      searchQuery: "",
      createDialogOpen: false,

      selectItem: (itemId) => set({ selectedItemId: itemId }),
      bindSkill: (itemId, skillId) =>
        set((s) => ({
          skillBindings: { ...s.skillBindings, [itemId]: skillId },
        })),
      unbindSkill: (itemId) =>
        set((s) => {
          const next = { ...s.skillBindings };
          delete next[itemId];
          return { skillBindings: next };
        }),
      setSearchQuery: (q) => set({ searchQuery: q }),
      openCreateDialog: () => set({ createDialogOpen: true }),
      closeCreateDialog: () => set({ createDialogOpen: false }),
    }),
    {
      name: "eaide.ops-nav",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({
        skillBindings: s.skillBindings,
        selectedItemId: s.selectedItemId,
      }),
    },
  ),
);
