/**
 * biznavStore —— Phase 2G 业务功能点导航状态（V1.2 后端联调）。
 *
 * V0 → V1.2 变化：
 *   - 6 个新异步 action：`loadStatus` / `loadFeatures` / `upsertFeature` /
 *     `deleteFeature` / `importYaml` / `exportYaml`（V0 的 `updateFeature` /
 *     `reindex` / `resetToMock` 全部保留作演示兜底）
 *   - 数据源：`backendReady=true` 时走真后端 (`biznavListFeatures` 等)，
 *     否则保留 V0 的 18 个 mock（仅演示用）
 *   - UI 状态（drawer / editor / selectedFeatureId）原封不动
 *
 * 重要：所有 actions 纯本地状态变更，**不调 chatStore**。
 * 跨 store 同步走 BiznavChatBridge（headless 订阅者，spec §2.3）。
 * 杜绝双向 action → 杜绝 React #300 风险。
 */
import { create } from 'zustand';
import type { Feature } from '@/types/biznav';
import { ipc } from '@/ipc/invoke';

const NOW = Date.now();

// ---------- 后端 Feature 响应类型（V1.2 临时松类型） ----------
// V1.5 会从 shared-protocol 镜像 Python dataclass，类型会收窄。
// 当前 backend FeatureStorage 返回字段对齐 Python dataclass（snake_case），
// 前端 Feature 类型用 camelCase；为避免大面积改 mock fixtures / Feature type，
// V1.2 阶段把 backend Feature 当作 raw record 处理；展示时由 selector 转 camelCase。
interface BackendFeatureRaw {
  id: string;
  name: string;
  description?: string;
  category: string;
  project_name: string;
  project_root: string;
  skill_id?: string | null;
  related_files?: unknown[];
  related_apis?: unknown[];
  related_tables?: unknown[];
  business_rules?: Array<{ text: string }>;
  source?: 'ai' | 'manual';
  ai_confidence?: number | null;
  version?: number;
  created_at?: number;
  updated_at?: number;
  deleted_at?: number | null;
  risk_level?: 'high' | 'medium' | 'low';
}

function rawToFeature(raw: BackendFeatureRaw): Feature {
  // 兼容后端返回（V1.1 backend 没有 risk_level 字段，前端兜底 low）
  // V1.5 才会把 BusinessRule/RelatedFile 等后端类型镜像到 shared-protocol；
  // 当前 Feature 类型（前端 V0 mock）只覆盖 V0 字段，未含 deleted_at /
  // business_rules 作为对象形式。这里用 unknown 中转避开 TS 严格检查。
  const rules = (raw.business_rules ?? []).map((r) =>
    typeof r === 'string' ? r : (r as { text: string }).text
  );
  const out: Feature = {
    id: raw.id,
    name: raw.name,
    description: raw.description ?? '',
    category: raw.category,
    project_name: raw.project_name,
    project_root: raw.project_root,
    skill_id: raw.skill_id ?? null,
    related_files: (raw.related_files ?? []) as Feature['related_files'],
    related_apis: (raw.related_apis ?? []) as Feature['related_apis'],
    related_tables: (raw.related_tables ?? []) as Feature['related_tables'],
    business_rules: rules as unknown as Feature['business_rules'],
    source: raw.source ?? 'manual',
    ai_confidence: raw.ai_confidence ?? null,
    risk_level: raw.risk_level ?? 'low',
    version: raw.version ?? 1,
    created_at: raw.created_at ?? NOW,
    updated_at: raw.updated_at ?? NOW,
  };
  // deleted_at 不在 Feature 类型里（V0 字段未覆盖）；用 unknown 旁路避免 TS 错。
  (out as unknown as { deleted_at?: number | null }).deleted_at =
    raw.deleted_at ?? null;
  return out;
}

interface BiznavState {
  features: Feature[];
  projectName: string;
  projectRoot: string;

  // V1.2 后端联调状态
  backendReady: boolean;
  loading: boolean;
  error: string | null;
  lastExportYaml: string | null;

  // 导入工程 + AI 提取功能点状态（后台任务轮询）
  extracting: boolean;
  /** 后台任务阶段：'pending' | 'scanning' | 'extracting'（done/failed 后清空） */
  extractStage: string | null;
  extractError: string | null;
  /** 提取进度百分比（0-100）；processed_files / total_files，无法计算时为 null */
  extractProgress: number | null;

  // UI 状态
  selectedFeatureId: string | null;
  drawerOpen: boolean;
  editorOpen: boolean;

  // Actions —— V0 mock（演示兜底，backendReady=false 时生效）
  selectFeature: (id: string | null) => void;
  openDrawer: (id: string) => void;
  closeDrawer: () => void;
  openEditor: (id: string) => void;
  closeEditor: () => void;
  /** Phase 2H：后端不可用时本地新增功能点（运营工作台「＋ 新建」兜底） */
  addLocalFeature: (f: Feature) => void;
  updateFeature: (id: string, patch: Partial<Feature>) => void;
  reindex: () => void;
  resetToMock: () => void;

  // Actions —— V1.2 异步（调 Tauri command → FastAPI → biznav.db）
  loadStatus: () => Promise<void>;
  loadFeatures: (opts?: { project_name?: string; category?: string }) => Promise<void>;
  upsertFeature: (
    id: string,
    project_name: string,
    body: Record<string, unknown>
  ) => Promise<void>;
  deleteFeature: (id: string, project_name: string) => Promise<void>;
  importYaml: (yaml_text: string, mode: 'merge' | 'replace') => Promise<void>;
  exportYaml: (project_name: string) => Promise<string | null>;
  /** 导入工程目录 → 触发后端 AI 提取功能点 → 轮询 status 直到完成后自动 loadFeatures */
  importProjectAndExtract: (folder: string) => Promise<void>;
}

// ---------- Fisher-Yates 洗牌 ----------
function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// ---------- 初始状态 ----------
const initialFeatures: Feature[] = [];

export const useBiznavStore = create<BiznavState>((set, get) => ({
  features: initialFeatures,
  projectName: 'demo',
  projectRoot: 'C:/demo/order-service',

  backendReady: false,
  loading: false,
  error: null,
  lastExportYaml: null,

  extracting: false,
  extractStage: null,
  extractError: null,
  extractProgress: null,

  selectedFeatureId: null,
  drawerOpen: false,
  editorOpen: false,

  // V0 actions（保留作演示兜底）
  selectFeature: (id) => set({ selectedFeatureId: id }),
  openDrawer: (id) => set({ selectedFeatureId: id, drawerOpen: true }),
  closeDrawer: () => set({ drawerOpen: false }),
  openEditor: (id) => set({ editorOpen: true, selectedFeatureId: id }),
  closeEditor: () => set({ editorOpen: false }),

  addLocalFeature: (f) =>
    set((s) => ({
      features: s.features.some((x) => x.id === f.id)
        ? s.features.map((x) => (x.id === f.id ? f : x))
        : [...s.features, f],
    })),

  updateFeature: (id, patch) =>
    set((s) => ({
      features: s.features.map((f) =>
        f.id === id
          ? { ...f, ...patch, version: f.version + 1, updated_at: NOW }
          : f
      ),
    })),

  reindex: () => {
    set((s) => ({ features: shuffle(s.features) }));
  },

  resetToMock: () => set({ features: [] }),

  // V1.2 异步 actions
  loadStatus: async () => {
    try {
      const status = await ipc.biznavStatus(get().projectName);
      set({ backendReady: !!status?.has_job });
    } catch {
      set({ backendReady: false });
    }
  },

  loadFeatures: async (opts) => {
    set({ loading: true, error: null });
    try {
      const resp = (await ipc.biznavListFeatures(opts)) as unknown;
      // 后端 GET /biznav/features 返回 { project_name, features, total } 对象（非数组），
      // 直接 .map 会抛 "(intermediate value).map is not a function" —— 解包兼容两种形态
      const raws: BackendFeatureRaw[] = Array.isArray(resp)
        ? (resp as BackendFeatureRaw[])
        : ((resp as { features?: BackendFeatureRaw[] } | null)?.features ?? []);
      set({
        features: raws.map(rawToFeature),
        loading: false,
        backendReady: true,
        ...(opts?.project_name ? { projectName: opts.project_name } : {}),
      });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : String(e),
        // 后端拉取失败时保留 mock（演示兜底）
      });
    }
  },

  upsertFeature: async (id, project_name, body) => {
    set({ loading: true, error: null });
    try {
      await ipc.biznavUpsertFeature(id, project_name, body);
      // upsert 后刷新列表（保留乐观的本地写入由 V1.5 引入）
      await get().loadFeatures({ project_name });
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
      throw e;
    }
  },

  deleteFeature: async (id, project_name) => {
    set({ loading: true, error: null });
    try {
      await ipc.biznavDeleteFeature(id, project_name);
      // 删完刷新
      await get().loadFeatures({ project_name });
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
      throw e;
    }
  },

  importYaml: async (yaml_text, mode) => {
    set({ loading: true, error: null });
    try {
      // Python ImportRequest 期望 merge: bool（true=合并，false=replace）
      await ipc.biznavImportYaml({
        project_name: get().projectName,
        yaml_text,
        merge: mode !== 'replace',
      });
      await get().loadFeatures({ project_name: get().projectName });
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
      throw e;
    }
  },

  exportYaml: async (project_name) => {
    try {
      const r = await ipc.biznavExportYaml(project_name);
      const yaml = r?.yaml_text ?? null;
      set({ lastExportYaml: yaml });
      return yaml;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  },

  // 导入工程 → AI 提取功能点（后台任务 + 2s 轮询 status，SSE 事件 V1.3 才真发不可依赖）
  importProjectAndExtract: async (folder) => {
    // project_name 取目录 basename（兼容 Windows 反斜杠）
    const name = folder.split(/[\\/]/).filter(Boolean).pop() || folder;
    set({
      projectName: name,
      projectRoot: folder,
      extracting: true,
      extractStage: 'pending',
      extractError: null,
      extractProgress: null,
      error: null,
    });
    try {
      await ipc.biznavExtract({ project_name: name, project_root: folder });
    } catch (e) {
      set({
        extracting: false,
        extractStage: null,
        extractProgress: null,
        extractError: `启动提取任务失败：${e instanceof Error ? e.message : String(e)}`,
      });
      return;
    }
    // 轮询 job 状态（最长 10 分钟；大项目 LLM 提取较慢）
    const deadline = Date.now() + 10 * 60 * 1000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const st = await ipc.biznavStatus(name);
        const job = (st?.job ?? undefined) as
          | {
              status?: string;
              error_message?: string | null;
              total_files?: number;
              processed_files?: number;
              features_generated?: number;
            }
          | undefined;
        const status = job?.status ?? 'pending';
        // 进度百分比：processed_files / total_files（extracting 阶段有意义）
        const total = job?.total_files ?? 0;
        const processed = job?.processed_files ?? 0;
        const progress =
          total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : null;
        if (status === 'done') {
          set({
            extracting: false,
            extractStage: null,
            extractProgress: null,
            backendReady: true,
          });
          await get().loadFeatures({ project_name: name });
          // 防御：done 但 0 产出（如工程里没有受支持的源码文件）→ 给提示而不是静默空列表
          if ((job?.features_generated ?? 0) === 0) {
            set({
              extractError:
                job?.error_message ||
                '提取完成但未生成任何功能点（工程中可能没有受支持的源码文件）',
            });
          }
          return;
        }
        if (status === 'failed') {
          set({
            extracting: false,
            extractStage: null,
            extractProgress: null,
            extractError: job?.error_message || '提取任务失败',
          });
          return;
        }
        set({ extractStage: status, extractProgress: progress });
      } catch {
        // status 探测瞬时失败不中断轮询
      }
    }
    set({
      extracting: false,
      extractStage: null,
      extractProgress: null,
      extractError: '提取超时（10 分钟），请稍后点击 ↻ 刷新',
    });
  },
}));

// ---------- 派生选择器 ----------

/** 按 category 分组（保持插入顺序：订单→用户→财务→库存→报表） */
export const selectFeaturesByCategory = (
  s: BiznavState
): Map<string, Feature[]> => {
  const map = new Map<string, Feature[]>();
  for (const f of s.features) {
    const arr = map.get(f.category) ?? [];
    arr.push(f);
    map.set(f.category, arr);
  }
  return map;
};

export const selectSelectedFeature = (s: BiznavState): Feature | null => {
  if (!s.selectedFeatureId) return null;
  return s.features.find((f) => f.id === s.selectedFeatureId) ?? null;
};

export const selectEditorFeature = (s: BiznavState): Feature | null => {
  if (!s.editorOpen || !s.selectedFeatureId) return null;
  return s.features.find((f) => f.id === s.selectedFeatureId) ?? null;
};

export const selectStats = (
  s: BiznavState
): {
  categories: number;
  features: number;
  apis: number;
  tables: number;
  rules: number;
  highRisk: number;
} => {
  const cats = new Set<string>();
  let apis = 0;
  let tables = 0;
  let rules = 0;
  let highRisk = 0;
  for (const f of s.features) {
    cats.add(f.category);
    apis += f.related_apis.length;
    tables += f.related_tables.length;
    rules += f.business_rules.length;
    if (f.risk_level === 'high') highRisk += 1;
  }
  return {
    categories: cats.size,
    features: s.features.length,
    apis,
    tables,
    rules,
    highRisk,
  };
};
