/**
 * opsCaseStore —— 专家验收工作流 Case 状态（2026-08-10）。
 *
 * 运营模式中间区取代传统大 Chat：
 *   选业务 → loadCase；客户经理给每位专家上传材料（FileReader 读 base64，
 *   不依赖 fs 插件权限）→ AI 专家审核 → 不懂就向专家迷你提问 →
 *   全部验收通过 → exportCase 打包交付物 zip。
 *
 * 数据源：后端 agent/ops/cases.py（ops.db + ops-cases 目录），IPC 走 opsCase*。
 */
import { create } from "zustand";
import type {
  OpsCaseDraft,
  OpsCaseFile,
  OpsCaseQa,
  OpsCaseState,
} from "@/types/ops";
import { ipc } from "@/ipc/invoke";

/** 单个专家维度上的进行中动作（驱动卡片内的思考动效） */
export type MemberBusyKind = "uploading" | "reviewing" | "asking";

function toFile(raw: Record<string, unknown>): OpsCaseFile {
  return raw as unknown as OpsCaseFile;
}

function toQa(raw: Record<string, unknown>): OpsCaseQa {
  return raw as unknown as OpsCaseQa;
}

function toDraft(raw: Record<string, unknown>): OpsCaseDraft {
  return {
    ...(raw as unknown as OpsCaseDraft),
    template: Array.isArray((raw as { template?: unknown }).template)
      ? ((raw as { template: OpsCaseDraft["template"] }).template)
      : [],
    values:
      raw.values && typeof raw.values === "object"
        ? (raw.values as Record<string, string>)
        : {},
  };
}

/** FileReader 读文件为纯 base64（去掉 dataURL 前缀） */
function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const idx = result.indexOf(",");
      resolve(idx >= 0 ? result.slice(idx + 1) : result);
    };
    reader.onerror = () => reject(new Error(`读取文件失败：${file.name}`));
    reader.readAsDataURL(file);
  });
}

interface OpsCaseStore extends OpsCaseState {
  loading: boolean;
  error: string | null;
  /** 按 memberKey 区分的进行中动作 */
  busyMembers: Record<string, MemberBusyKind | undefined>;
  exporting: boolean;
  /** 各专家团未读审核结果数（上传后出结果 → 页签红点提示；切到该页签时清零） */
  unreadByTeam: Record<string, number>;

  loadCase: (projectName: string, featureId: string) => Promise<void>;
  reset: () => void;
  /** 清空 Case 重新开始办理（BUGFIX #85）：后端删材料/问答/草稿后重载空 Case */
  clearCase: (projectName: string, featureId: string) => Promise<boolean>;
  /** 用户查看某专家团页签 → 清除该团未读提示 */
  markTeamRead: (teamId: string) => void;
  attachFiles: (
    teamId: string,
    memberKey: string,
    files: File[],
  ) => Promise<void>;
  reviewFile: (fileId: string) => Promise<void>;
  overrideFile: (fileId: string, status: string, note?: string) => Promise<void>;
  deleteFile: (fileId: string) => Promise<void>;
  askExpert: (teamId: string, memberKey: string, question: string) => Promise<void>;
  /** 交付草稿（BUGFIX #78）：保存填写值 / 提交审核 */
  saveDraft: (draftId: string, values: Record<string, string>) => Promise<void>;
  submitDraft: (draftId: string) => Promise<boolean>;
  exportCase: (
    targetPath: string,
    meta: { featureName?: string; teamName?: string; teamId?: string; checklist?: string[] },
  ) => Promise<boolean>;
  clearError: () => void;
  /** 静默重拉 Case（审核失败后同步后端状态） */
  reloadQuiet: () => Promise<void>;
}

function setBusy(
  memberKey: string,
  kind: MemberBusyKind | undefined,
): Partial<OpsCaseStore> {
  const st = useOpsCaseStore.getState();
  return { busyMembers: { ...st.busyMembers, [memberKey]: kind } };
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Agent 未就绪的瞬态错误（启动竞态：前端请求早于 Agent 监听 8765） */
function isTransient(e: unknown): boolean {
  const m = errMsg(e).toLowerCase();
  return (
    m.includes("error sending request") || // Rust reqwest 连接失败（拒绝/超时/DNS）
    m.includes("connection refused") ||
    m.includes("failed to fetch") ||
    m.includes("network")
  );
}

/** 瞬态失败的重试退避（累计约 15s，足够覆盖 Agent 冷启动） */
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000];

/** loadCase 序号令牌：业务切换时旧重试不得覆盖新 Case */
let loadCaseSeq = 0;

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export const useOpsCaseStore = create<OpsCaseStore>((set, get) => ({
  case_id: "",
  files: [],
  qa: [],
  drafts: [],
  loading: false,
  error: null,
  busyMembers: {},
  exporting: false,
  unreadByTeam: {},

  loadCase: async (projectName, featureId) => {
    if (!featureId) {
      get().reset();
      return;
    }
    set({ loading: true, error: null });
    const seq = ++loadCaseSeq;
    // 启动竞态（BUGFIX #77）：前端恢复上次选中业务后立即拉 Case，
    // 若 Agent 还没监听 8765 会连接失败 —— 瞬态错误静默退避重试，
    // 不向用户弹错；重试期间业务已切换则放弃（避免覆盖新 Case）
    for (let attempt = 0; ; attempt++) {
      try {
        const r = await ipc.opsCaseGet({ projectName, featureId });
        if (loadCaseSeq !== seq) return; // 返回时业务已切换，丢弃旧结果
        set({
          case_id: r.case_id,
          files: (r.files ?? []).map(toFile),
          qa: (r.qa ?? []).map(toQa),
          drafts: (r.drafts ?? []).map(toDraft),
          loading: false,
          // 初次加载的历史结果不算未读（避免一进页面满屏红点）
          unreadByTeam: {},
        });
        return;
      } catch (e) {
        if (isTransient(e) && attempt < RETRY_DELAYS_MS.length) {
          await sleep(RETRY_DELAYS_MS[attempt]);
          if (loadCaseSeq !== seq) return; // 重试期间已切到其他业务，放弃本次
          continue;
        }
        if (loadCaseSeq !== seq) return;
        set({ loading: false, error: errMsg(e) });
        return;
      }
    }
  },

  reset: () =>
    set({
      case_id: "",
      files: [],
      qa: [],
      drafts: [],
      busyMembers: {},
      error: null,
      unreadByTeam: {},
    }),

  clearCase: async (projectName, featureId) => {
    try {
      await ipc.opsCaseClear({ projectName, featureId });
      get().reset();
      // 重载空 Case：case_id 恢复可用，后续上传/提问不受影响
      await get().loadCase(projectName, featureId);
      return true;
    } catch (e) {
      set({ error: errMsg(e) });
      return false;
    }
  },

  markTeamRead: (teamId) =>
    set((s) => {
      if (!s.unreadByTeam[teamId]) return {};
      const next = { ...s.unreadByTeam };
      delete next[teamId];
      return { unreadByTeam: next };
    }),

  attachFiles: async (teamId, memberKey, files) => {
    const caseId = get().case_id;
    if (!caseId || files.length === 0) return;
    set(setBusy(memberKey, "uploading"));
    try {
      for (const file of files) {
        const content = await readFileAsBase64(file);
        const row = await ipc.opsCaseFileAdd({
          case_id: caseId,
          team_id: teamId,
          member_key: memberKey,
          file_name: file.name,
          content_base64: content,
        });
        set((s) => ({ files: [...s.files, toFile(row)] }));
        // 上传即触发 AI 专家审核（不阻塞后续文件上传，fire-and-forget 后统一刷新）
        void get()
          .reviewFile(toFile(row).id)
          .catch(() => undefined);
      }
    } catch (e) {
      set({ error: errMsg(e) });
    } finally {
      set(setBusy(memberKey, undefined));
    }
  },

  reviewFile: async (fileId) => {
    const target = get().files.find((f) => f.id === fileId);
    if (!target) return;
    set(setBusy(target.member_key, "reviewing"));
    set((s) => ({
      files: s.files.map((f) =>
        f.id === fileId ? { ...f, status: "reviewing" as const } : f,
      ),
    }));
    try {
      const row = await ipc.opsCaseFileReview(fileId);
      const updated = toFile(row);
      set((s) => ({
        files: s.files.map((f) => (f.id === fileId ? updated : f)),
        // 审核出结果 → 对应专家团页签未读 +1（用户切到该页签时清零）
        unreadByTeam: updated.team_id
          ? {
              ...s.unreadByTeam,
              [updated.team_id]: (s.unreadByTeam[updated.team_id] ?? 0) + 1,
            }
          : s.unreadByTeam,
      }));
    } catch (e) {
      // 审核失败 → 回退 pending 并提示（后端已写 review_note）
      set((s) => ({
        error: errMsg(e),
        files: s.files.map((f) =>
          f.id === fileId && f.status === "reviewing"
            ? { ...f, status: "pending" as const }
            : f,
        ),
      }));
      await get().reloadQuiet();
    } finally {
      set(setBusy(target.member_key, undefined));
    }
  },

  overrideFile: async (fileId, status, note) => {
    try {
      const row = await ipc.opsCaseFileOverride(fileId, {
        status,
        ...(note ? { note } : {}),
      });
      set((s) => ({
        files: s.files.map((f) => (f.id === fileId ? toFile(row) : f)),
      }));
    } catch (e) {
      set({ error: errMsg(e) });
    }
  },

  deleteFile: async (fileId) => {
    try {
      await ipc.opsCaseFileDelete(fileId);
      set((s) => ({ files: s.files.filter((f) => f.id !== fileId) }));
    } catch (e) {
      set({ error: errMsg(e) });
    }
  },

  askExpert: async (teamId, memberKey, question) => {
    const caseId = get().case_id;
    if (!caseId || !question.trim()) return;
    set(setBusy(memberKey, "asking"));
    try {
      const r = await ipc.opsCaseAsk({
        case_id: caseId,
        team_id: teamId,
        member_key: memberKey,
        question: question.trim(),
      });
      // 模板/清单类回答会附带可直填草稿（BUGFIX #78）：同步进草稿列表
      set((s) => ({
        qa: [...s.qa, toQa(r.qa)],
        drafts: r.draft ? [...s.drafts, toDraft(r.draft)] : s.drafts,
      }));
    } catch (e) {
      set({ error: errMsg(e) });
    } finally {
      set(setBusy(memberKey, undefined));
    }
  },

  saveDraft: async (draftId, values) => {
    try {
      const row = await ipc.opsCaseDraftSave(draftId, { values });
      set((s) => ({
        drafts: s.drafts.map((d) => (d.id === draftId ? toDraft(row) : d)),
      }));
    } catch (e) {
      set({ error: errMsg(e) });
    }
  },

  submitDraft: async (draftId) => {
    const target = get().drafts.find((d) => d.id === draftId);
    if (!target) return false;
    set(setBusy(target.member_key, "reviewing"));
    try {
      const r = await ipc.opsCaseDraftSubmit(draftId);
      set((s) => ({
        drafts: s.drafts.map((d) => (d.id === draftId ? toDraft(r.draft) : d)),
        // 草稿提交后成为材料：同步/追加文件行，审核意见在专家卡里看
        files: s.files.some((f) => f.id === toFile(r.file).id)
          ? s.files.map((f) => (f.id === toFile(r.file).id ? toFile(r.file) : f))
          : [...s.files, toFile(r.file)],
      }));
      return true;
    } catch (e) {
      set({ error: errMsg(e) });
      return false;
    } finally {
      set(setBusy(target.member_key, undefined));
    }
  },

  exportCase: async (targetPath, meta) => {
    const caseId = get().case_id;
    if (!caseId) return false;
    set({ exporting: true, error: null });
    try {
      await ipc.opsCaseExport({
        case_id: caseId,
        target_path: targetPath,
        ...(meta.featureName ? { feature_name: meta.featureName } : {}),
        ...(meta.teamName ? { team_name: meta.teamName } : {}),
        ...(meta.teamId ? { team_id: meta.teamId } : {}),
        checklist: meta.checklist ?? [],
      });
      set({ exporting: false });
      return true;
    } catch (e) {
      set({ exporting: false, error: errMsg(e) });
      return false;
    }
  },

  clearError: () => set({ error: null }),

  reloadQuiet: async () => {
    const st = get();
    if (!st.case_id) return;
    try {
      const [projectName, featureId] = st.case_id.split("__");
      const r = await ipc.opsCaseGet({ projectName, featureId });
      set({
        files: (r.files ?? []).map(toFile),
        qa: (r.qa ?? []).map(toQa),
        drafts: (r.drafts ?? []).map(toDraft),
      });
    } catch {
      // 静默：仅用于审核失败后同步后端状态
    }
  },
}));
