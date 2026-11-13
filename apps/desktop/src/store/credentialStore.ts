/**
 * credentialStore —— 凭证预览状态（2026-11-09）。
 *
 * 一个 Case 的凭证视图：专家团预设的凭证节点 + 实挂的凭证资产
 * （已审材料 CF-* / OCR 识别底图 CI-*）+ 要素采集情况 + 齐备度统计。
 *
 * 数据源：后端 agent/ops/api.py 的 /ops/case/credentials 等端点，IPC 走
 * opsCaseCredentials / opsCredentialAsset*。业务办理面板与业务汇总页共用
 * 同一个 store（汇总页传 readOnly，写操作在 store 层不拦、由 UI 不入口拦）。
 *
 * 影像定位：选中资产时一次拉回图片字节 + RapidOCR blocks（缓存），
 * 点要素名 → locateValue(value, blocks) → highlight（框由组件绘制）。
 */
import { create } from "zustand";
import { ipc } from "@/ipc/invoke";
import { locateValue, type OcrRect } from "@/lib/ocrLocate";
import type {
  CredentialAsset,
  CredentialFieldValue,
  CredentialOcrMeta,
  CredentialStats,
  CredentialView,
  OcrBlock,
} from "@/types/ops";

/** 凭证节点主键：团::专家::凭证名 */
export function credentialNodeKey(
  teamId: string,
  memberKey: string,
  credential: string,
): string {
  return `${teamId}::${memberKey}::${credential}`;
}

/** 已加载的凭证资产明细（图片 data URL + 识别块，供影像工作区渲染） */
export interface CredentialAssetDetail {
  assetId: string;
  kind: string;
  fileName: string;
  mime: string;
  /** 非图片（PDF/Word）时为空串：没有位图可画，只能列要素 */
  dataUrl: string;
  width: number;
  height: number;
  blocks: OcrBlock[];
  engine: string;
  ocrError: string;
}

/** 影像高亮目标（点要素名后写入，组件按视口尺寸算 transform） */
export interface CredentialHighlight {
  assetId: string;
  rect: OcrRect;
  matched: string[];
  confidence: number;
  how: "exact" | "normalized";
  /** 触发序号：同一个字段连点也要重新放大居中一次 */
  seq: number;
}

interface CredentialStoreState {
  caseId: string;
  teamIds: string[];
  view: CredentialView | null;
  loading: boolean;
  classifying: boolean;
  error: string | null;
  /** 左栏选中的凭证节点 */
  selectedNodeKey: string;
  /** 影像工作区当前资产 */
  selectedAssetId: string;
  asset: CredentialAssetDetail | null;
  assetLoading: boolean;
  highlight: CredentialHighlight | null;
  /** 定位失败/无影像时的一句话说明（不说谎：手动录入就是手动录入） */
  locateNote: string;
  busyAssetId: string;

  load: (
    caseId: string,
    teamIds?: string[],
    opts?: { silent?: boolean },
  ) => Promise<void>;
  selectNode: (nodeKey: string) => Promise<void>;
  selectAsset: (assetId: string) => Promise<void>;
  classify: (body: { force?: boolean }) => Promise<void>;
  assign: (assetId: string, credential: string) => Promise<void>;
  recognize: (assetId: string) => Promise<void>;
  removeAsset: (assetId: string) => Promise<void>;
  locate: (field: CredentialFieldValue) => Promise<void>;
  reset: () => void;
}

const EMPTY_STATS: CredentialStats = {
  required_total: 0,
  required_ok: 0,
  missing: [],
  has_preset: false,
  asset_total: 0,
  unassigned_total: 0,
};

/** 后端统计 → 完整 CredentialStats（旧后端 / 降级返回缺字段时不会整页报错） */
export function normalizeCredentialStats(raw: unknown): CredentialStats {
  if (raw === null || raw === undefined) return EMPTY_STATS;
  const rec = raw as Record<string, unknown>;
  const missing = rec.missing;
  return {
    required_total: Number(rec.required_total ?? 0) || 0,
    required_ok: Number(rec.required_ok ?? 0) || 0,
    missing: Array.isArray(missing)
      ? (missing as CredentialStats["missing"])
      : [],
    has_preset: !!rec.has_preset,
    asset_total: Number(rec.asset_total ?? 0) || 0,
    unassigned_total: Number(rec.unassigned_total ?? 0) || 0,
  };
}

function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function toView(raw: unknown): CredentialView {
  const rec = (raw ?? {}) as Record<string, unknown>;
  return {
    case_id: String(rec.case_id ?? ""),
    teams: Array.isArray(rec.teams)
      ? (rec.teams as CredentialView["teams"])
      : [],
    stats: normalizeCredentialStats(rec.stats),
  };
}

/** 把后端 ocr 元数据规整成渲染需要的形状（blocks 只留坐标合法的） */
function normalizeOcr(meta: CredentialOcrMeta | undefined): {
  width: number;
  height: number;
  blocks: OcrBlock[];
  engine: string;
  error: string;
} {
  const m = meta ?? {};
  const blocks = Array.isArray(m.blocks)
    ? m.blocks.filter((b) => Array.isArray(b?.bbox))
    : [];
  return {
    width: Number(m.width ?? 0) || 0,
    height: Number(m.height ?? 0) || 0,
    blocks,
    engine: String(m.engine ?? ""),
    error: String(m.error ?? ""),
  };
}

export const useCredentialStore = create<CredentialStoreState>((set, get) => ({
  caseId: "",
  teamIds: [],
  view: null,
  loading: false,
  classifying: false,
  error: null,
  selectedNodeKey: "",
  selectedAssetId: "",
  asset: null,
  assetLoading: false,
  highlight: null,
  locateNote: "",
  busyAssetId: "",

  load: async (caseId, teamIds = [], opts) => {
    if (!opts?.silent) set({ loading: true, error: null });
    try {
      const raw = await ipc.opsCaseCredentials(caseId, teamIds);
      const view = toView(raw);
      const patch: Partial<CredentialStoreState> = {
        caseId,
        teamIds,
        view,
        loading: false,
        error: null,
      };
      // 节点被删/首次打开：选中第一个「有资产」的节点，其次第一个必传缺失的节点
      const nodes = collectNodes(view);
      const stillThere = !get().selectedNodeKey
        ? false
        : nodes.some((n) => n.key === get().selectedNodeKey);
      if (!stillThere) {
        const first =
          nodes.find((n) => n.assets.length > 0) ??
          nodes.find((n) => n.required && n.assets.length === 0) ??
          nodes[0];
        patch.selectedNodeKey = first?.key ?? "";
        patch.selectedAssetId = first?.assets[0]?.id ?? "";
        patch.asset = null;
        patch.highlight = null;
        patch.locateNote = "";
      }
      set(patch);
      // 弹窗自己打开（非 silent）时才把默认影像拉出来；
      // 办理面板只要一个齐备度数字，刷新走 silent，不能为此拉几 MB 图。
      const pendingAsset = get().selectedAssetId;
      if (!opts?.silent && pendingAsset && !get().asset) {
        await get().selectAsset(pendingAsset);
      }
    } catch (e) {
      set({ loading: false, error: errorMessage(e) });
    }
  },

  selectNode: async (nodeKey) => {
    const view = get().view;
    const node = collectNodes(view).find((n) => n.key === nodeKey);
    set({
      selectedNodeKey: nodeKey,
      selectedAssetId: node?.assets[0]?.id ?? "",
      highlight: null,
      locateNote: "",
    });
    if (node?.assets[0]) await get().selectAsset(node.assets[0].id);
    else set({ asset: null });
  },

  selectAsset: async (assetId) => {
    if (!assetId) {
      set({
        selectedAssetId: "",
        asset: null,
        highlight: null,
        locateNote: "",
      });
      return;
    }
    if (get().selectedAssetId === assetId && get().asset) return;
    set({
      selectedAssetId: assetId,
      assetLoading: true,
      highlight: null,
      locateNote: "",
    });
    try {
      const r = await ipc.opsCredentialAssetGet(assetId);
      const ocr = normalizeOcr(r.ocr as CredentialOcrMeta);
      const isImage = r.mime.startsWith("image/");
      set({
        assetLoading: false,
        asset: {
          assetId: r.id,
          kind: r.kind,
          fileName: r.file_name,
          mime: r.mime,
          dataUrl: isImage ? `data:${r.mime};base64,${r.content_base64}` : "",
          width: ocr.width,
          height: ocr.height,
          blocks: ocr.blocks,
          engine: ocr.engine,
          ocrError: ocr.error,
        },
        locateNote: isImage
          ? ""
          : "这份凭证不是图片（如 PDF/文档），无法在影像上框出区域，只能核对要素。",
      });
    } catch (e) {
      set({ assetLoading: false, asset: null, error: errorMessage(e) });
    }
  },

  classify: async (body) => {
    const { caseId, teamIds } = get();
    if (!caseId) return;
    set({ classifying: true, error: null });
    try {
      await ipc.opsCaseClassifyCredentials({
        case_id: caseId,
        team_ids: teamIds,
        force: !!body?.force,
      });
      set({ classifying: false });
      await get().load(caseId, teamIds, { silent: true });
    } catch (e) {
      set({ classifying: false, error: errorMessage(e) });
    }
  },

  assign: async (assetId, credential) => {
    const { caseId, teamIds } = get();
    try {
      await ipc.opsCredentialAssetAssign(assetId, credential);
      await get().load(caseId, teamIds, { silent: true });
    } catch (e) {
      set({ error: errorMessage(e) });
    }
  },

  recognize: async (assetId) => {
    const { caseId, teamIds } = get();
    set({ busyAssetId: assetId, error: null });
    try {
      await ipc.opsCredentialAssetRecognize(assetId);
      // 重新拉该资产明细（blocks 到手后才能定位）
      const selected = get().selectedAssetId === assetId;
      if (selected) set({ selectedAssetId: "" });
      await get().load(caseId, teamIds, { silent: true });
      if (selected) await get().selectAsset(assetId);
    } catch (e) {
      set({ error: errorMessage(e) });
    } finally {
      set({ busyAssetId: "" });
    }
  },

  removeAsset: async (assetId) => {
    const { caseId, teamIds } = get();
    try {
      await ipc.opsCredentialAssetDelete(assetId);
      if (get().selectedAssetId === assetId) {
        set({ selectedAssetId: "", asset: null, highlight: null });
      }
      await get().load(caseId, teamIds, { silent: true });
    } catch (e) {
      set({ error: errorMessage(e) });
    }
  },

  locate: async (field) => {
    const note = (msg: string): void =>
      void set({ highlight: null, locateNote: msg });
    if (!field.can_locate || field.asset_kind === "draft") {
      note(
        field.source === "manual"
          ? `「${field.label}」是手动录入的，凭证上没有对应区域。`
          : `「${field.label}」的来源凭证还没有影像识别块，无法定位。`,
      );
      return;
    }
    if (get().selectedAssetId !== field.asset_id) {
      await get().selectAsset(field.asset_id);
    }
    const asset = get().asset;
    if (!asset || asset.assetId !== field.asset_id) {
      note("影像读取失败，换个节点或点「立即识别影像」再试。");
      return;
    }
    if (asset.blocks.length === 0) {
      note(
        "这张影像还没有识别块：先点「立即识别影像」（端侧 OCR，约 10-30 秒）。",
      );
      return;
    }
    const hit = locateValue(field.value, asset.blocks);
    if (!hit) {
      note(
        `识别原文里没找到「${field.value}」：可能是手动改过值，或这张图识别不清。`,
      );
      return;
    }
    set({
      locateNote: "",
      highlight: {
        assetId: asset.assetId,
        rect: hit.rect,
        matched: hit.matched,
        confidence: hit.confidence,
        how: hit.how,
        seq: (get().highlight?.seq ?? 0) + 1,
      },
    });
  },

  reset: () =>
    set({
      caseId: "",
      teamIds: [],
      view: null,
      loading: false,
      classifying: false,
      error: null,
      selectedNodeKey: "",
      selectedAssetId: "",
      asset: null,
      assetLoading: false,
      highlight: null,
      locateNote: "",
      busyAssetId: "",
    }),
}));

/** 展开成扁平节点列表（左栏渲染与选中态判断共用） */
export interface FlatCredentialNode {
  key: string;
  teamId: string;
  teamName: string;
  memberKey: string;
  name: string;
  required: boolean;
  assets: CredentialAsset[];
}

export function collectNodes(
  view: CredentialView | null,
): FlatCredentialNode[] {
  const out: FlatCredentialNode[] = [];
  for (const team of view?.teams ?? []) {
    for (const member of team.members) {
      for (const node of member.credentials) {
        out.push({
          key: credentialNodeKey(team.team_id, member.member_key, node.name),
          teamId: team.team_id,
          teamName: team.team_name,
          memberKey: member.member_key,
          name: node.name,
          required: node.required,
          assets: node.assets,
        });
      }
    }
  }
  return out;
}
