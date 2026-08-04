import { create } from "zustand";
import { ipc } from "@/ipc/invoke";
import type {
  DocDetail,
  DocFinding,
  DocRiskLevel,
  DocSummary,
} from "@eaide/shared-protocol";

export const DOC_REVIEW_RISK_COLORS: Record<
  DocRiskLevel,
  { badge: string; bg: string }
> = {
  critical: { badge: "#b3261e", bg: "rgba(179,38,30,0.28)" },
  high: { badge: "#cd3131", bg: "rgba(205,49,49,0.22)" },
  medium: { badge: "#b25c1a", bg: "rgba(178,92,26,0.22)" },
  low: { badge: "#059669", bg: "rgba(5,150,105,0.18)" },
};

interface DocReviewState {
  docs: DocSummary[];
  selectedDocId: string | null;
  detail: DocDetail | null;
  findings: DocFinding[];
  analyzing: boolean;
  error: string | null;

  loadDocs: () => Promise<void>;
  register: (filePath: string) => Promise<{ ok: boolean; error?: string }>;
  open: (docId: string) => Promise<void>;
  analyze: (docId: string) => Promise<void>;
  pollStatus: (docId: string) => Promise<void>;
  selectFinding: (findingId: string | null) => void;
}

export const useDocReviewStore = create<DocReviewState>((set, get) => ({
  docs: [],
  selectedDocId: null,
  detail: null,
  findings: [],
  analyzing: false,
  error: null,

  loadDocs: async () => {
    try {
      const docs = await ipc.docReviewList();
      set({ docs });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },

  register: async (filePath) => {
    try {
      await ipc.docReviewRegister(filePath);
      await get().loadDocs();
      return { ok: true };
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  },

  open: async (docId) => {
    set({ selectedDocId: docId, analyzing: true, error: null });
    try {
      const detail = await ipc.docReviewGet(docId);
      set({ detail, findings: detail.findings });
      if (detail.status === "none" || detail.status === "failed") {
        await get().analyze(docId);
      } else if (
        detail.status === "queued" ||
        detail.status === "classifying" ||
        detail.status === "analyzing"
      ) {
        await get().pollStatus(docId);
      }
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    } finally {
      set({ analyzing: false });
    }
  },

  analyze: async (docId) => {
    try {
      await ipc.docReviewAnalyze(docId);
      await get().pollStatus(docId);
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },

  pollStatus: async (docId) => {
    // V0 轮询；SSE 事件已就位，后续可切换为订阅
    for (let i = 0; i < 120; i += 1) {
      await new Promise((r) => setTimeout(r, 1000));
      const st = await ipc.docReviewStatus(docId);
      if (st.status === "done") {
        const detail = await ipc.docReviewGet(docId);
        set({ detail, findings: detail.findings, analyzing: false });
        return;
      }
      if (st.status === "failed") {
        set({ error: st.error ?? "分析失败", analyzing: false });
        return;
      }
    }
    set({ error: "分析超时", analyzing: false });
  },

  selectFinding: (findingId) => {
    const el = findingId
      ? document.getElementById(`finding-${findingId}`)
      : null;
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  },
}));
