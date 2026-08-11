/**
 * skillsStore —— Phase 2D Skill 状态。
 * V1 接后端 /skills/list 等 FastAPI 端点。
 */
import { create } from 'zustand';
import type { Skill } from '@/types/skill';
import { ipc } from '@/ipc/invoke';

interface SkillsState {
  skills: Skill[];
  selectedSkillId: string | null;
  editorOpen: boolean;
  importDialogOpen: boolean;

  // Actions
  loadSkills: () => Promise<void>;
  setSkills: (skills: Skill[]) => void;
  selectSkill: (id: string | null) => void;
  openEditor: (id: string) => void;
  closeEditor: () => void;
  openImportDialog: () => void;
  closeImportDialog: () => void;
  /** Phase 2H：保存并落盘后端（失败时保留本地兜底） */
  saveSkill: (id: string, body: Skill) => void;
  /** 删除（后端 + 本地） */
  deleteSkill: (id: string) => void;
  toggleEnabled: (id: string) => void;
  /** 导入（后端 + 本地） */
  importSkill: (body: Skill) => void;
  createSkill: () => void;
  resetToMock: () => void;
}

export const useSkillsStore = create<SkillsState>((set) => ({
  skills: [],
  selectedSkillId: null,
  editorOpen: false,
  importDialogOpen: false,

  loadSkills: async () => {
    try {
      const r = await ipc.skillsList();
      set({ skills: (r.skills ?? []).map((s) => s as unknown as Skill) });
    } catch {
      // Agent 未就绪时保留空列表（运营工作台会显示「暂无 Skill」提示）
    }
  },
  setSkills: (skills) => set({ skills }),
  selectSkill: (id) => set({ selectedSkillId: id }),
  openEditor: (id) => set({ editorOpen: true, selectedSkillId: id }),
  closeEditor: () => set({ editorOpen: false }),
  openImportDialog: () => set({ importDialogOpen: true }),
  closeImportDialog: () => set({ importDialogOpen: false }),

  saveSkill: (id, body) => {
    // 后端落盘（PUT /skills/{id}，新 skill 也会写 YAML + 重载）
    void ipc
      .skillsSave(id, body as unknown as Record<string, unknown>)
      .then(() => {
        useSkillsStore.getState().loadSkills();
      })
      .catch(() => undefined);
    set((s) => ({
      skills: s.skills.map((sk) =>
        sk.id === id ? { ...body, loaded_at: Date.now() } : sk
      ),
      editorOpen: false,
    }));
  },

  deleteSkill: (id) => {
    void ipc.skillsDelete(id).catch(() => undefined);
    set((s) => ({
      skills: s.skills.filter((sk) => sk.id !== id),
      selectedSkillId: s.selectedSkillId === id ? null : s.selectedSkillId,
    }));
  },

  toggleEnabled: (id) => {
    const current = useSkillsStore.getState().skills.find((sk) => sk.id === id);
    if (current) {
      // 后端 PUT 持久化启停状态（失败静默，本地先切）
      void ipc
        .skillsSave(id, { ...current, enabled: !current.enabled } as unknown as Record<string, unknown>)
        .catch(() => undefined);
    }
    set((s) => ({
      skills: s.skills.map((sk) =>
        sk.id === id ? { ...sk, enabled: !sk.enabled } : sk
      ),
    }));
  },

  importSkill: (body) => {
    void ipc
      .skillsImport(body as unknown as Record<string, unknown>)
      .then(() => {
        useSkillsStore.getState().loadSkills();
      })
      .catch(() => undefined);
    set((s) => ({
      skills: [...s.skills, { ...body, loaded_at: Date.now() }],
      importDialogOpen: false,
    }));
  },

  /** Phase 2H：新建空白 Skill 并打开编辑器（功能点编辑器「＋ 新建」入口） */
  createSkill: () => {
    const id = `skill_${Date.now().toString(36)}`;
    const blank: Skill = {
      schema_version: '1.0',
      id,
      name: '新 Skill',
      description: '',
      version: '1.0',
      author: '',
      tags: [],
      risk_level: 'low',
      enabled: true,
      trigger_keywords: [],
      mcp_servers: [],
      allowed_tools: [],
      role: 'utility',
      system_prompt: '',
      few_shot_examples: [],
      required_expert_team_ids: [],
      materials: [],
      deliverables: [],
      source_path: '',
      loaded_at: Date.now(),
      validation_errors: [],
    };
    set((s) => ({ skills: [...s.skills, blank], editorOpen: true, selectedSkillId: id }));
  },

  resetToMock: () => set({ skills: [] }),
}));

export const selectSkillById = (id: string) => (s: SkillsState): Skill | null =>
  s.skills.find((sk) => sk.id === id) ?? null;
