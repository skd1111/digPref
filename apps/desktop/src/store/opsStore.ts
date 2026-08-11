/**
 * opsStore —— Phase 2H 运营工作台业务记录状态。
 *
 * 数据源：后端 agent/ops（ops.db），IPC 走 ops* wrapper。
 * 「生成业务记录」：把当前会话最近消息 + 功能点 + Skill 经验发给后端
 * /ops/records/summarize（LLM 三级降级链与 biznav/reqflow 一致），
 * 生成草稿后由人工确认保存（可审计）。
 */
import { create } from "zustand";
import type { BusinessRecord, OpsRecordDraft } from "@/types/ops";
import { ipc } from "@/ipc/invoke";

function toRecord(raw: Record<string, unknown>): BusinessRecord {
  return raw as unknown as BusinessRecord;
}

interface OpsState {
  records: BusinessRecord[];
  loading: boolean;
  generating: boolean;
  error: string | null;

  loadRecords: (opts?: {
    featureId?: string;
    projectName?: string;
  }) => Promise<void>;
  createRecord: (
    body: Record<string, unknown>,
  ) => Promise<BusinessRecord | null>;
  deleteRecord: (recordId: string) => Promise<boolean>;
  summarizeDraft: (params: {
    featureId: string;
    projectName: string;
    businessType?: string;
    conversation: Array<{ role: string; content: string }>;
    sessionId?: string;
  }) => Promise<OpsRecordDraft | null>;
  clearError: () => void;
}

export const useOpsStore = create<OpsState>((set, get) => ({
  records: [],
  loading: false,
  generating: false,
  error: null,

  loadRecords: async (opts) => {
    set({ loading: true, error: null });
    try {
      const r = await ipc.opsListRecords({
        ...(opts?.featureId ? { featureId: opts.featureId } : {}),
        ...(opts?.projectName ? { projectName: opts.projectName } : {}),
      });
      set({ records: (r.records ?? []).map(toRecord), loading: false });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  },

  createRecord: async (body) => {
    set({ error: null });
    try {
      const raw = await ipc.opsCreateRecord(body);
      const rec = toRecord(raw);
      set({ records: [rec, ...get().records] });
      return rec;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  deleteRecord: async (recordId) => {
    try {
      await ipc.opsDeleteRecord(recordId);
      set((s) => ({ records: s.records.filter((r) => r.id !== recordId) }));
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return false;
    }
  },

  summarizeDraft: async (params) => {
    set({ generating: true, error: null });
    try {
      const r = await ipc.opsSummarizeRecord({
        feature_id: params.featureId,
        project_name: params.projectName,
        ...(params.businessType ? { business_type: params.businessType } : {}),
        conversation: params.conversation,
        ...(params.sessionId ? { session_id: params.sessionId } : {}),
      });
      set({ generating: false });
      return (r.draft ?? null) as unknown as OpsRecordDraft | null;
    } catch (e) {
      set({
        generating: false,
        error: e instanceof Error ? e.message : String(e),
      });
      return null;
    }
  },

  clearError: () => set({ error: null }),
}));
