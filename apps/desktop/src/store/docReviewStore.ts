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

/** 分析完成后的风险点统计摘要（用于完成横幅） */
export interface AnalysisSummary {
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
}

interface DocReviewState {
  docs: DocSummary[];
  selectedDocId: string | null;
  detail: DocDetail | null;
  findings: DocFinding[];
  /** 真正在跑分析（后端有进行中的 run）；驱动遮罩/模糊化/进度轮询 */
  analyzing: boolean;
  /** 正在读取后端已持久化的文档详情（切换/打开文档的加载态）。
   *  与 analyzing 严格区分：读取已完成文档的结果绝不是「重新分析」 */
  loading: boolean;
  error: string | null;
  /** 正在导入的文件名（null = 空闲）。doc 需经 Word/WPS COM 转换，可达 120s，UI 必须展示进度动效 */
  importingFile: string | null;
  /** 本轮分析完成摘要（null = 无/已关闭；开始新分析时清空） */
  analysisSummary: AnalysisSummary | null;
  /** 当前分析进度 0..1（null = 无进度信息） */
  progress: number | null;

  loadDocs: () => Promise<void>;
  register: (
    filePath: string,
  ) => Promise<{ ok: boolean; error?: string; doc_id?: string | null }>;
  /** 删除文档（后端级联清 findings / analysis_runs；不删磁盘源文件） */
  remove: (docId: string) => Promise<{ ok: boolean; error?: string }>;
  open: (docId: string) => Promise<void>;
  /** 手动触发分析（列表按钮）：选中文档 + 加载详情 + 启动分析 */
  analyzeDoc: (docId: string) => Promise<void>;
  analyze: (docId: string) => Promise<void>;
  pollStatus: (docId: string) => Promise<void>;
  selectFinding: (findingId: string | null) => void;
  dismissAnalysisSummary: () => void;
}

export const useDocReviewStore = create<DocReviewState>((set, get) => ({
  docs: [],
  selectedDocId: null,
  detail: null,
  findings: [],
  analyzing: false,
  loading: false,
  error: null,
  analysisSummary: null,
  progress: null,
  importingFile: null,

  loadDocs: async () => {
    try {
      const docs = await ipc.docReviewList();
      set({ docs });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },

  register: async (filePath) => {
    const fileName = filePath.split(/[\\/]/).pop() ?? filePath;
    set({ importingFile: fileName });
    try {
      const r = await ipc.docReviewRegister(filePath);
      await get().loadDocs();
      return { ok: true, doc_id: r?.doc_id ?? null };
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      };
    } finally {
      set({ importingFile: null });
    }
  },

  remove: async (docId) => {
    try {
      await ipc.docReviewDelete(docId);
      // 删的是当前选中文档 → 清空右侧详情/发现，避免残留展示
      if (get().selectedDocId === docId) {
        set({
          selectedDocId: null,
          detail: null,
          findings: [],
          analyzing: false,
          loading: false,
          analysisSummary: null,
          progress: null,
        });
      }
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
    // 切换/打开文档 = 读取后端已持久化的分析结果，绝不重新触发分析。
    // 读取详情的 GET 会因逐条 finding 附加知识库依据（RAG）而耗时数秒，
    // 期间用 loading 态展示「加载中」，不能误置 analyzing 让 UI 显示「正在分析」。
    set({
      selectedDocId: docId,
      detail: null,
      findings: [],
      loading: true,
      analyzing: false,
      error: null,
      analysisSummary: null,
      progress: null,
    });
    try {
      const detail = await ipc.docReviewGet(docId);
      // 仅当后端确有进行中的 run 时才算「在分析」，续接进度轮询；
      // done / failed / none 都是已落库的终态，直接展示持久化结果。
      const inProgress =
        detail.status === "queued" ||
        detail.status === "classifying" ||
        detail.status === "analyzing";
      set({ detail, findings: detail.findings, loading: false, analyzing: inProgress });
      if (inProgress) {
        await get().pollStatus(docId);
      }
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        loading: false,
        analyzing: false,
      });
    }
  },

  analyzeDoc: async (docId) => {
    set({
      selectedDocId: docId,
      loading: true,
      analysisSummary: null,
      error: null,
      progress: null,
    });
    try {
      const detail = await ipc.docReviewGet(docId);
      set({ detail, findings: detail.findings, loading: false });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err), loading: false });
      return;
    }
    await get().analyze(docId);
  },

  analyze: async (docId) => {
    try {
      set({ analysisSummary: null, progress: null, analyzing: true, error: null });
      await ipc.docReviewAnalyze(docId);
      await get().pollStatus(docId);
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        analyzing: false,
      });
    }
  },

  pollStatus: async (docId) => {
    // 轮询直到后端终态（done / failed）。分析是后端异步任务：
    // 大文档 = 风险维度 × 分块数 × 单次 LLM 调用，可达数十分钟，
    // 绝不能按固定轮数（如 120s）误报"分析超时"中断前端等待。
    // 唯一超时条件：进度与状态连续 STALE_LIMIT_MS 无任何变化（后端真卡死）。
    const POLL_MS = 2000;
    const STALE_LIMIT_MS = 10 * 60 * 1000;
    let lastProgress = -1;
    let lastStatus = "";
    let staleMs = 0;
    let consecutiveErrors = 0;
    for (;;) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      // 用户切走/删除了该文档 → 退出本轮轮询
      if (get().selectedDocId !== docId) return;
      let st: Awaited<ReturnType<typeof ipc.docReviewStatus>>;
      try {
        st = await ipc.docReviewStatus(docId);
        consecutiveErrors = 0;
      } catch {
        consecutiveErrors += 1;
        // Agent 短暂抖动容忍；连续 15 次（约 30s）不通才报错
        if (consecutiveErrors >= 15) {
          set({
            error: "无法连接 Agent，分析状态未知（Agent 可能已重启）",
            analyzing: false,
            progress: null,
          });
          return;
        }
        continue;
      }
      if (typeof st.progress === "number") {
        set({ progress: st.progress });
      }
      const progressNow = typeof st.progress === "number" ? st.progress : -1;
      if (progressNow > lastProgress || st.status !== lastStatus) {
        staleMs = 0;
        lastProgress = Math.max(lastProgress, progressNow);
        lastStatus = st.status;
      } else {
        staleMs += POLL_MS;
        if (staleMs >= STALE_LIMIT_MS) {
          set({
            error: "分析长时间无进展，后端可能已卡死，请点击重新分析",
            analyzing: false,
            progress: null,
          });
          return;
        }
      }
      if (st.status === "done") {
        const detail = await ipc.docReviewGet(docId);
        const list = detail.findings ?? [];
        const summary: AnalysisSummary = {
          total: list.length,
          critical: list.filter((f) => f.risk_level === "critical").length,
          high: list.filter((f) => f.risk_level === "high").length,
          medium: list.filter((f) => f.risk_level === "medium").length,
          low: list.filter((f) => f.risk_level === "low").length,
        };
        // 同步刷新列表内该文档的状态/风险等级：否则列表还是旧快照，
        // "分析"按钮不会变成"重新分析"，风险等级徽章也不显示
        set((state) => ({
          docs: state.docs.map((d) =>
            d.doc_id === docId
              ? {
                  ...d,
                  status: "done",
                  overall_risk_level: detail.overall_risk_level ?? null,
                  doc_category: detail.doc_category ?? null,
                }
              : d,
          ),
        }));
        set({
          detail,
          findings: detail.findings,
          analyzing: false,
          analysisSummary: summary,
          progress: 1,
        });
        return;
      }
      if (st.status === "failed") {
        set((state) => ({
          docs: state.docs.map((d) =>
            d.doc_id === docId ? { ...d, status: "failed" } : d,
          ),
        }));
        set({ error: st.error ?? "分析失败", analyzing: false, progress: null });
        return;
      }
    }
  },

  selectFinding: (findingId) => {
    const el = findingId
      ? document.getElementById(`finding-${findingId}`)
      : null;
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    // 定位后闪烁强调，避免高亮淹没在正文里找不到（CSS .finding-mark-flash）
    el.classList.remove("finding-mark-flash");
    void (el as HTMLElement).offsetWidth; // 强制重排，连续点击也能重播
    el.classList.add("finding-mark-flash");
    window.setTimeout(() => el.classList.remove("finding-mark-flash"), 2200);
  },

  dismissAnalysisSummary: () => set({ analysisSummary: null }),
}));
