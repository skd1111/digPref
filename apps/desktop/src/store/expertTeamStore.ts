/**
 * expertTeamStore —— 专家团资产状态（系统一等资产，不以 Skill 形式存在）。
 *
 * - teams：后端 %APPDATA%\eaide\expert_teams\*.yaml 的镜像（真源在后端）
 * - 选择态：selectedTeamIds + selectionMode（会话级，不持久化；重启回落 auto）
 *   - auto：跟随业务（Skill 预设 / AI 推荐）
 *   - manual：用户在输入栏手选，切换业务不再自动改写
 */
import { create } from 'zustand';
import type { ExpertTeam } from '@/types/expertTeam';
import { ipc } from '@/ipc/invoke';

export type ExpertTeamSelectionSource =
  | 'preset'
  | 'llm'
  | 'keyword'
  | 'none'
  | 'manual';

interface ExpertTeamState {
  teams: ExpertTeam[];
  selectedTeamIds: string[];
  selectionMode: 'auto' | 'manual';
  /** 当前选择的来源（供 UI 标注「业务预设 / AI 推荐 / 手动」） */
  selectionSource: ExpertTeamSelectionSource | '';
  /** 当前选择对应的业务功能点 id（会话级）：同一业务切模式再切回时直接复用，
   *  不再重跑 LLM 推荐（避免重复加载刚打开的专家团） */
  selectedForItemId: string | null;
  /** AI 推荐进行中（recommend 走 LLM 需数秒，UI 据此展示加载动效避免「假卡死」） */
  recommending: boolean;

  editorOpen: boolean;
  editingTeamId: string | null;
  importDialogOpen: boolean;

  loadTeams: () => Promise<void>;
  saveTeam: (id: string, body: ExpertTeam) => void;
  deleteTeam: (id: string) => void;
  /** 导入 YAML 文本（后端解析，兼容旧格式）；失败返回 error 信息 */
  importTeamYamlText: (content: string) => Promise<{ ok: boolean; error?: string }>;
  /** 导入专家团资产包 zip（team.yaml 提示词 + templates/ 交付物模板，2026-08-10） */
  importTeamPackage: (file: File) => Promise<{ ok: boolean; error?: string }>;
  toggleEnabled: (id: string) => void;

  /** 自动选择（业务预设 / AI 推荐）；manual 模式下调用方应自行跳过。
   *  forItemId：该选择对应的业务功能点（供切模式返回时复用判断） */
  applyAutoSelection: (
    ids: string[],
    source: ExpertTeamSelectionSource,
    forItemId?: string | null,
  ) => void;
  /** AI 推荐生命周期：开始（recommending=true）/ 结束（false） */
  setRecommending: (v: boolean) => void;
  /** 手动选团；forItemId 缺省沿用当前业务（选择器不感知业务上下文） */
  selectManually: (ids: string[], forItemId?: string | null) => void;
  clearSelection: () => void;

  openEditor: (id: string | null) => void;
  closeEditor: () => void;
  openImportDialog: () => void;
  closeImportDialog: () => void;
}

export const useExpertTeamStore = create<ExpertTeamState>((set) => ({
  teams: [],
  selectedTeamIds: [],
  selectionMode: 'auto',
  selectionSource: '',
  selectedForItemId: null,
  recommending: false,

  editorOpen: false,
  editingTeamId: null,
  importDialogOpen: false,

  loadTeams: async () => {
    try {
      const r = await ipc.expertTeamsList();
      set({ teams: (r.teams ?? []).map((t) => t as unknown as ExpertTeam) });
    } catch {
      // Agent 未就绪时保留空列表（与 skillsStore 一致，不阻塞 UI）
    }
  },

  saveTeam: (id, body) => {
    void ipc
      .expertTeamsSave(id, body as unknown as Record<string, unknown>)
      .then(() => {
        useExpertTeamStore.getState().loadTeams();
      })
      .catch(() => undefined);
    set((s) => ({
      teams: s.teams.some((t) => t.id === id)
        ? s.teams.map((t) => (t.id === id ? { ...body, loaded_at: Date.now() } : t))
        : [...s.teams, { ...body, loaded_at: Date.now() }],
      editorOpen: false,
      editingTeamId: null,
    }));
  },

  deleteTeam: (id) => {
    void ipc.expertTeamsDelete(id).catch(() => undefined);
    set((s) => ({
      teams: s.teams.filter((t) => t.id !== id),
      selectedTeamIds: s.selectedTeamIds.filter((tid) => tid !== id),
      // 删掉的团恰是当前选择的唯一团 → 选择失效，业务 id 一并清空（下次进入重新选）
      selectedForItemId:
        s.selectedTeamIds.length === 1 && s.selectedTeamIds[0] === id
          ? null
          : s.selectedForItemId,
    }));
  },

  importTeamYamlText: async (content) => {
    try {
      await ipc.expertTeamsImport({ content });
      await useExpertTeamStore.getState().loadTeams();
      set({ importDialogOpen: false });
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
  },

  importTeamPackage: async (file) => {
    try {
      // 读 zip 为纯 base64（去掉 dataURL 前缀）
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ''));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      const base64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
      await ipc.expertTeamsImportPackage(file.name, base64);
      await useExpertTeamStore.getState().loadTeams();
      set({ importDialogOpen: false });
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
  },

  toggleEnabled: (id) => {
    const current = useExpertTeamStore.getState().teams.find((t) => t.id === id);
    if (current) {
      void ipc
        .expertTeamsSave(id, {
          ...current,
          enabled: !current.enabled,
        } as unknown as Record<string, unknown>)
        .catch(() => undefined);
    }
    set((s) => ({
      teams: s.teams.map((t) => (t.id === id ? { ...t, enabled: !t.enabled } : t)),
    }));
  },

  applyAutoSelection: (ids, source, forItemId) =>
    set((s) => ({
      selectedTeamIds: ids,
      selectionMode: 'auto',
      selectionSource: source,
      selectedForItemId: forItemId === undefined ? s.selectedForItemId : forItemId,
      recommending: false,
    })),

  setRecommending: (v) => set({ recommending: v }),

  selectManually: (ids, forItemId) =>
    set((s) => ({
      selectedTeamIds: ids,
      selectionMode: 'manual',
      selectionSource: 'manual',
      selectedForItemId: forItemId === undefined ? s.selectedForItemId : forItemId,
    })),

  clearSelection: () =>
    set({
      selectedTeamIds: [],
      selectionMode: 'auto',
      selectionSource: '',
      selectedForItemId: null,
    }),

  openEditor: (id) => set({ editorOpen: true, editingTeamId: id }),
  closeEditor: () => set({ editorOpen: false, editingTeamId: null }),
  openImportDialog: () => set({ importDialogOpen: true }),
  closeImportDialog: () => set({ importDialogOpen: false }),
}));

export const selectTeamById = (id: string) => (s: ExpertTeamState): ExpertTeam | null =>
  s.teams.find((t) => t.id === id) ?? null;
