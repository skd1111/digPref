/**
 * skillsStore —— Phase 2D Skill 状态。
 * V1 接后端 /skills/list 等 FastAPI 端点。
 */
import { create } from 'zustand';
import type { Skill } from '@/types/skill';

interface SkillsState {
  skills: Skill[];
  selectedSkillId: string | null;
  editorOpen: boolean;
  importDialogOpen: boolean;

  // Actions
  setSkills: (skills: Skill[]) => void;
  selectSkill: (id: string | null) => void;
  openEditor: (id: string) => void;
  closeEditor: () => void;
  openImportDialog: () => void;
  closeImportDialog: () => void;
  saveSkill: (id: string, body: Skill) => void;
  deleteSkill: (id: string) => void;
  toggleEnabled: (id: string) => void;
  importSkill: (body: Skill) => void;
  resetToMock: () => void;
}

export const useSkillsStore = create<SkillsState>((set) => ({
  skills: [],
  selectedSkillId: null,
  editorOpen: false,
  importDialogOpen: false,

  setSkills: (skills) => set({ skills }),
  selectSkill: (id) => set({ selectedSkillId: id }),
  openEditor: (id) => set({ editorOpen: true, selectedSkillId: id }),
  closeEditor: () => set({ editorOpen: false }),
  openImportDialog: () => set({ importDialogOpen: true }),
  closeImportDialog: () => set({ importDialogOpen: false }),

  saveSkill: (id, body) =>
    set((s) => ({
      skills: s.skills.map((sk) =>
        sk.id === id ? { ...body, loaded_at: Date.now() } : sk
      ),
      editorOpen: false,
    })),

  deleteSkill: (id) =>
    set((s) => ({
      skills: s.skills.filter((sk) => sk.id !== id),
      selectedSkillId: s.selectedSkillId === id ? null : s.selectedSkillId,
    })),

  toggleEnabled: (id) =>
    set((s) => ({
      skills: s.skills.map((sk) =>
        sk.id === id ? { ...sk, enabled: !sk.enabled } : sk
      ),
    })),

  importSkill: (body) =>
    set((s) => ({
      skills: [...s.skills, { ...body, loaded_at: Date.now() }],
      importDialogOpen: false,
    })),

  resetToMock: () => set({ skills: [] }),
}));

export const selectSkillById = (id: string) => (s: SkillsState): Skill | null =>
  s.skills.find((sk) => sk.id === id) ?? null;
