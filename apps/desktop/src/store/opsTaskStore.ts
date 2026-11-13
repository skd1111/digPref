/**
 * opsTaskStore —— 业务办理台账状态（业务汇总，2026-11-09）。
 *
 * 数据源：后端 agent/ops/tasks.py（ops.db 的 business_tasks 表），IPC 走 opsTask*。
 *
 * 与 opsStore（业务记录卡片）的区别：
 *   - 业务记录卡片：业务**办完之后** AI 生成的小结；
 *   - 本台账：业务**发起即建**、状态随办理流转（正在办理 / 挂起 / 人工确认 / 结束）。
 */
import { create } from "zustand";
import type {
  OpsBusinessTask,
  OpsTaskDetail,
  OpsTaskListResult,
  OpsTaskQuery,
  OpsTaskStatus,
} from "@/types/ops";
import { OPS_TASK_STATUSES } from "@/types/ops";
import { normalizeCredentialStats } from "@/store/credentialStore";
import { ipc } from "@/ipc/invoke";

function toTask(raw: unknown): OpsBusinessTask {
  const rec = (raw ?? {}) as Record<string, unknown>;
  const pending = rec.pending_items;
  return {
    ...(rec as unknown as OpsBusinessTask),
    pending_items: Array.isArray(pending)
      ? (pending as OpsBusinessTask["pending_items"])
      : [],
  };
}

function emptyCounts(): Record<OpsTaskStatus, number> {
  return {
    in_progress: 0,
    suspended: 0,
    manual_review: 0,
    closed: 0,
  };
}

interface OpsTaskStore {
  tasks: OpsBusinessTask[];
  counts: Record<OpsTaskStatus, number>;
  loading: boolean;
  error: string | null;
  detail: OpsTaskDetail | null;
  /** 当前筛选条件（业务汇总页） */
  query: OpsTaskQuery;

  loadTasks: (query?: OpsTaskQuery) => Promise<void>;
  loadDetail: (taskId: string) => Promise<void>;
  setQuery: (patch: Partial<OpsTaskQuery>) => void;
  resetQuery: () => void;
  clearError: () => void;
}

export const useOpsTaskStore = create<OpsTaskStore>((set, get) => ({
  tasks: [],
  counts: emptyCounts(),
  loading: false,
  error: null,
  detail: null,
  query: {},

  loadTasks: async (query) => {
    const q = query ?? get().query;
    set({ loading: true, error: null, query: q });
    try {
      const r = (await ipc.opsTaskList({
        status:
          q.status && q.status.length > 0 ? q.status.join(",") : undefined,
        category: q.category || undefined,
        featureId: q.feature_id || undefined,
        projectName: q.project_name || undefined,
        caseId: q.case_id || undefined,
        dateFrom: q.date_from,
        dateTo: q.date_to,
        keyword: q.keyword || undefined,
        limit: q.limit,
      })) as unknown as OpsTaskListResult;
      const counts = emptyCounts();
      for (const s of OPS_TASK_STATUSES) {
        counts[s] = Number(r.counts?.[s] ?? 0);
      }
      set({
        tasks: (r.tasks ?? []).map(toTask),
        counts,
        loading: false,
      });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  },

  loadDetail: async (taskId) => {
    try {
      const r = await ipc.opsTaskGet(taskId);
      set({
        detail: {
          task: toTask(r.task ?? {}),
          pending_items: (r.pending_items ??
            []) as unknown as OpsTaskDetail["pending_items"],
          file_stats: r.file_stats ?? {},
          file_total: r.file_total ?? 0,
          record: (r.record ?? null) as unknown as OpsTaskDetail["record"],
          // 凭证摘要（凭证预览）：旧后端不返该字段时补齐为零齐备度，视图不会撞 undefined
          credentials: normalizeCredentialStats(r.credentials),
        },
      });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },

  setQuery: (patch) => {
    const next = { ...get().query, ...patch };
    set({ query: next });
    void get().loadTasks(next);
  },

  resetQuery: () => {
    set({ query: {} });
    void get().loadTasks({});
  },

  clearError: () => set({ error: null }),
}));
