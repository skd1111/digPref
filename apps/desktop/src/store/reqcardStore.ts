/**
 * reqcardStore —— 运营专家需求改造工作流（需求卡片 V1）状态。
 *
 * 数据源：后端 agent/reqflow（reqcards.db），IPC 走 reqflow* wrapper。
 * 状态流转规则前端镜像 STATUS_TRANSITIONS（后端是权威，409 时回滚并报错）。
 * 版本：列表/详情永远最新版；loadVersions/viewVersion 查看历史快照（只读）。
 */
import { create } from 'zustand';
import type { CardVersionMeta, ReqBatch, ReqCard } from '@/types/reqcard';
import { ipc } from '@/ipc/invoke';

/** 需求对齐会话状态（功能点树发起 → 对话 → 生成卡片） */
interface AlignmentState {
  active: boolean;
  featureIds: string[];
}

interface ReqcardState {
  batches: ReqBatch[];
  batchStats: Record<string, Record<string, number>>;
  currentBatchId: string | null;
  cards: ReqCard[];
  selectedCardId: string | null;

  // AI 生成 / 保存状态
  generating: boolean;
  saving: boolean;
  error: string | null;

  // 历史版本
  versions: CardVersionMeta[];
  viewingVersion: number | null;
  versionSnapshot: ReqCard | null;

  // 需求对齐
  alignment: AlignmentState;
  /** 本工程已完成（done）的需求卡片：对齐对话/生成卡片时的参照 */
  doneCards: ReqCard[];

  // Actions
  loadBatches: (projectName?: string) => Promise<void>;
  createBatch: (projectName: string, name?: string) => Promise<ReqBatch | null>;
  selectBatch: (batchId: string | null) => Promise<void>;
  loadCards: (opts?: {
    batchId?: string;
    status?: string;
    featureId?: string;
  }) => Promise<void>;
  saveCard: (body: Record<string, unknown>) => Promise<ReqCard | null>;
  updateCard: (cardId: string, fields: Record<string, unknown>) => Promise<boolean>;
  deleteCard: (cardId: string) => Promise<boolean>;
  loadVersions: (cardId: string) => Promise<void>;
  viewVersion: (cardId: string, version: number) => Promise<void>;
  backToLatest: () => void;
  generateCardDraft: (params: {
    featureIds: string[];
    projectName: string;
    systemName?: string;
    conversationSummary: string;
    sessionId?: string;
  }) => Promise<Record<string, unknown> | null>;
  /** 一键生成并保存：确保批次存在 → AI 生成草稿 → 落库为 draft 卡片并选中 */
  generateAndSaveCard: (params: {
    featureIds: string[];
    projectName: string;
    systemName?: string;
    conversationSummary: string;
    sessionId?: string;
  }) => Promise<ReqCard | null>;
  /** 拉取本工程已完成的需求卡片（对齐会话与卡片生成时参照，避免重复/冲突） */
  loadDoneCards: (projectName: string) => Promise<void>;
  startAlignment: (featureIds: string[], projectName?: string) => void;
  cancelAlignment: () => void;
  exportBatch: (
    batchId: string,
    format: 'md' | 'docx',
  ) => Promise<{ markdown?: string; base64?: string; filename?: string } | null>;
  clearError: () => void;
}

function toCard(raw: Record<string, unknown>): ReqCard {
  return raw as unknown as ReqCard;
}

function toBatch(raw: Record<string, unknown>): ReqBatch {
  return raw as unknown as ReqBatch;
}

export const useReqcardStore = create<ReqcardState>((set, get) => ({
  batches: [],
  batchStats: {},
  currentBatchId: null,
  cards: [],
  selectedCardId: null,

  generating: false,
  saving: false,
  error: null,

  versions: [],
  viewingVersion: null,
  versionSnapshot: null,

  alignment: { active: false, featureIds: [] },
  doneCards: [],

  loadBatches: async (projectName) => {
    try {
      const r = await ipc.reqflowListBatches(projectName);
      set({
        batches: (r.batches ?? []).map(toBatch),
        batchStats: r.stats ?? {},
        error: null,
      });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },

  createBatch: async (projectName, name) => {
    try {
      const raw = await ipc.reqflowCreateBatch({
        project_name: projectName,
        ...(name ? { name } : {}),
      });
      const batch = toBatch(raw);
      await get().loadBatches(projectName);
      set({ currentBatchId: batch.id, error: null });
      return batch;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  selectBatch: async (batchId) => {
    set({
      currentBatchId: batchId,
      selectedCardId: null,
      versions: [],
      viewingVersion: null,
      versionSnapshot: null,
    });
    if (batchId) {
      await get().loadCards({ batchId });
    } else {
      set({ cards: [] });
    }
  },

  loadCards: async (opts) => {
    const batchId = opts?.batchId ?? get().currentBatchId ?? undefined;
    try {
      const r = await ipc.reqflowListCards({
        ...(batchId ? { batchId } : {}),
        ...(opts?.status ? { status: opts.status } : {}),
        ...(opts?.featureId ? { featureId: opts.featureId } : {}),
      });
      set({ cards: (r.cards ?? []).map(toCard), error: null });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },

  saveCard: async (body) => {
    set({ saving: true, error: null });
    try {
      const raw = await ipc.reqflowCreateCard(body);
      const card = toCard(raw);
      set({ saving: false });
      await get().loadCards();
      set({ selectedCardId: card.id });
      return card;
    } catch (e) {
      set({ saving: false, error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  updateCard: async (cardId, fields) => {
    try {
      const raw = await ipc.reqflowUpdateCard(cardId, fields);
      const updated = toCard(raw);
      // 原地替换列表项（后端已记版本）
      set((s) => ({
        cards: s.cards.map((c) => (c.id === cardId ? updated : c)),
        error: null,
        versions: [],
        viewingVersion: null,
        versionSnapshot: null,
      }));
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },

  deleteCard: async (cardId) => {
    try {
      await ipc.reqflowDeleteCard(cardId);
      set((s) => ({
        cards: s.cards.filter((c) => c.id !== cardId),
        selectedCardId:
          s.selectedCardId === cardId ? null : s.selectedCardId,
        error: null,
      }));
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },

  loadVersions: async (cardId) => {
    try {
      const r = await ipc.reqflowListCardVersions(cardId);
      set({ versions: r.versions ?? [], error: null });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },

  viewVersion: async (cardId, version) => {
    try {
      const r = await ipc.reqflowGetCardVersion(cardId, version);
      set({
        viewingVersion: version,
        versionSnapshot: toCard(r.snapshot),
        error: null,
      });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },

  backToLatest: () => set({ viewingVersion: null, versionSnapshot: null }),

  generateCardDraft: async (params) => {
    set({ generating: true, error: null });
    try {
      const r = await ipc.reqflowGenerateCard({
        feature_ids: params.featureIds,
        project_name: params.projectName,
        ...(params.systemName ? { system_name: params.systemName } : {}),
        conversation_summary: params.conversationSummary,
        ...(params.sessionId ? { session_id: params.sessionId } : {}),
      });
      set({ generating: false });
      return r.draft ?? null;
    } catch (e) {
      set({
        generating: false,
        error: e instanceof Error ? e.message : String(e),
      });
      return null;
    }
  },

  startAlignment: (featureIds, projectName) => {
    set({ alignment: { active: true, featureIds } });
    // 对齐会话期间注入已完成需求参照（发送时拼进 prompt）
    if (projectName) {
      void get().loadDoneCards(projectName);
    }
  },

  loadDoneCards: async (projectName) => {
    try {
      const r = await ipc.reqflowListCards({ projectName, status: 'done' });
      set({ doneCards: (r.cards ?? []).map(toCard) });
    } catch {
      // 拉取失败不阻塞对齐（只是少了参照）
      set({ doneCards: [] });
    }
  },

  generateAndSaveCard: async (params) => {
    // 0. 刷新已完成需求参照（生成时注入提示词）
    await get().loadDoneCards(params.projectName);
    // 1. 确保批次存在：先查当前工程的批次，没有就建一个默认批次
    await get().loadBatches(params.projectName);
    let batchId = get().currentBatchId;
    const projectBatches = get().batches.filter(
      (b) => b.project_name === params.projectName && b.status === 'open',
    );
    if (!batchId || !projectBatches.some((b) => b.id === batchId)) {
      batchId = projectBatches[0]?.id ?? null;
    }
    if (!batchId) {
      const batch = await get().createBatch(params.projectName);
      if (!batch) return null;
      batchId = batch.id;
    }
    // 2. AI 生成草稿
    const draft = await get().generateCardDraft(params);
    if (!draft) return null;
    // 3. 落库为 draft 卡片
    const card = await get().saveCard({
      batch_id: batchId,
      project_name: params.projectName,
      system_name: params.systemName ?? '',
      title: draft.title,
      feature_ids: params.featureIds,
      business_value: draft.business_value ?? '',
      change_points: draft.change_points ?? '',
      feasibility: draft.feasibility ?? '',
      feasibility_notes: draft.feasibility_notes ?? '',
      impact: draft.impact ?? '',
      external_systems: draft.external_systems ?? [],
      priority: draft.priority ?? 'P2',
      conversation_summary: params.conversationSummary,
      session_id: params.sessionId ?? '',
    });
    if (card) {
      set({ currentBatchId: batchId });
      await get().loadBatches(params.projectName);
    }
    return card;
  },

  cancelAlignment: () => set({ alignment: { active: false, featureIds: [] } }),

  exportBatch: async (batchId, format) => {
    try {
      const r = await ipc.reqflowExport(batchId, format);
      set({ error: null });
      return r;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  clearError: () => set({ error: null }),
}));

/**
 * 已完成需求提示词片段：对齐会话发送时拼进 prompt，
 * 让 AI 对照已完成的改造分析重叠/冲突与影响面。
 */
export function buildDoneCardsSnippet(): string {
  const { doneCards, alignment } = useReqcardStore.getState();
  if (!alignment.active || doneCards.length === 0) return '';
  const lines: string[] = [
    '【本系统已完成的需求参考】（分析新需求时必须对照这些已完成的改造，'
    + '重叠/冲突要在回答中指出，影响分析要基于这些现状）',
  ];
  for (const c of doneCards.slice(-20)) {
    lines.push(`- ${c.id} · ${c.title}`);
    if (c.change_points) lines.push(`  改造点：${c.change_points.slice(0, 200)}`);
    if (c.impact && c.impact !== '无') lines.push(`  影响：${c.impact.slice(0, 200)}`);
    if (c.external_systems.length > 0) {
      lines.push(`  外部系统：${c.external_systems.slice(0, 5).join('、')}`);
    }
  }
  return lines.join('\n');
}
