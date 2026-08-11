/**
 * uiStore —— 全局 UI 状态（状态栏、命令面板、cheat sheet、模态框等共享状态）。
 *
 * 这是轻量"开关 + 标志位"集合。Tab / chat / trace 等业务状态留在各自 store。
 */
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type WorkMode = 'full' | 'operator' | 'auditor' | 'analyst';

/**
 * Phase 2F V1 收尾（2026-07-29）：ActivityId **单源定义**。
 *
 * V1 之前：ActivityId 在 `apps/desktop/src/store/uiStore.ts:64` 和
 * `apps/desktop/src/components/chrome/ActivityBar.tsx:16` 双源定义 ——
 * 新增 activity 必须同时改两处 + ITEMS 数组 + WorkspaceLayout TITLES + Outlet 分支，
 * 漏改任一处会导致运行期或 TypeScript 报错。
 *
 * V1 收尾后：单源在 uiStore；ActivityBar.tsx / WorkspaceLayout.tsx 等 `import` 消费。
 * 任意新增 activity → 改 uiStore 这一行 + ActivityBar ITEMS + WorkspaceLayout
 * TITLES + Outlet 三处联动（ITEMS / TITLES 改用 `satisfies ActivityId[]` 静态验证）。
 */
export type ActivityId =
  | 'explorer'
  | 'search'
  | 'source-control'
  | 'run-debug'
  // reqflow V1：需求工作台（运营专家模式，需求卡片管理）
  | 'requirements'
  | 'extensions'
  | 'collab'
  | 'code-nav'
  | 'sessions'
  // 数据字典：独立顶级入口（公共参数维护，原运营工作台右侧 tab 迁出）
  | 'data-dict';

/**
 * Phase 2H：开发模式左侧栏三态子切换。
 *   'assets'   —— 系统资产（DB / API / SSH / RPA）
 *   'files'    —— 文件列表（工程目录树，界面与右侧思维链保持现状）
 *   'features' —— 系统功能点（工程 AI 提炼，支持搜索，右侧切换为需求卡片）
 */
export type DevPanelMode = 'assets' | 'files' | 'features';

interface UIState {
  // 命令面板 / cheat sheet
  commandPaletteOpen: boolean;
  quickOpenOpen: boolean;
  cheatSheetOpen: boolean;

  // 状态栏：当前 Agent 状态、错误数
  agentStatus: 'idle' | 'busy' | 'error' | 'ready' | 'unknown';
  errorCount: number;
  warnCount: number;

  // 当前"焦点"位置（状态栏用，演示用）
  cursorLine: number;
  cursorCol: number;

  // 编辑器拆分：null 单栏，'vertical' 左右拆，'horizontal' 上下拆
  editorSplit: null | 'vertical' | 'horizontal';
  // 副栏的 active tab（null = 副栏隐藏或跟主栏同步）
  secondaryTabId: string | null;

  // 工作模式：
  //   'full'    = 开发模式（4 象限）
  //   'operator'= 运营模式（Phase 2H 收尾：恢复为独立顶级页签，与开发模式并列；
  //               进入后全屏渲染运营工作台：业务列表 + Chat + 工作台）
  //   'auditor' = 审核专家模式（Phase 5：金融级审批与审计，三栏布局）
  //   'analyst' = 数据专家模式（Phase 7：NL2SQL + 数据网格 + 图表 + 报表导出，四象限）
  // 持久化到 localStorage：用户上次关闭时的 mode 会保留到下次启动
  // 首次安装 / 清空 localStorage 时默认为 'full'（开发模式）
  // 迁移：旧版 'audit' 模式自动映射为 'auditor'（Phase 5 改名以区分原"文档审核"语义）
  mode: WorkMode;

  // Phase 2E 快捷键逃生通道（Ctrl+~）：半屏 Xterm 抽屉
  xtermDrawerOpen: boolean;

  // 切到运营专家模式时是否已勾选"下次不再提示"（持久化）
  operatorModePromptDismissed: boolean;

  // 切到审核专家模式时是否已勾选"下次不再提示"（持久化，独立于 operator）
  auditorModePromptDismissed: boolean;

  // 切到数据专家模式时是否已勾选"下次不再提示"（持久化，独立于 operator/auditor）
  analystModePromptDismissed: boolean;

  // Phase 5 TopBar Badge：待审批数量（来自 /audit/tasks 轮询，0 = 不显示）
  pendingAuditCount: number;

  // Phase 9 TopBar Badge：@ 我的未读数（来自 collabStore mock 推送，0 = 不显示）
  pendingCollabMentionCount: number;

  // ActivityBar 当前选中项（从 WorkspaceLayout 提升到 uiStore 以便跨组件重置）
  // 不持久化：刷新后默认 explorer（与原 WorkspaceLayout 行为一致）
  // 类型定义见上方 export type ActivityId（V1 单源；ActivityBar.tsx import 消费）
  activityId: ActivityId;

  // Phase 2G V1.3：左侧 SideBar 显示内容模式
  //   Phase 2H 起由 devPanelMode 接管：'assets' | 'files' | 'features'
  //   （保留字段用于 localStorage 旧值迁移）
  // 持久化：用户上次的选择保留到下次启动
  leftPanelMode: 'auto' | 'system' | 'business';

  // Phase 2H：开发模式左侧栏子切换（系统资产 / 文件列表 / 系统功能点）
  devPanelMode: DevPanelMode;

  // 右侧控制台面板宽度（可拖拽调节，240–720，默认 320；持久化）
  rightPanelWidth: number;

  // 操作
  toggleCommandPalette: (open?: boolean) => void;
  toggleQuickOpen: (open?: boolean) => void;
  toggleCheatSheet: (open?: boolean) => void;
  setAgentStatus: (s: UIState['agentStatus']) => void;
  setErrorCount: (n: number) => void;
  setWarnCount: (n: number) => void;
  setCursor: (line: number, col: number) => void;
  setEditorSplit: (m: UIState['editorSplit']) => void;
  setSecondaryTabId: (id: string | null) => void;
  setMode: (m: WorkMode) => void;
  toggleXtermDrawer: (open?: boolean) => void;
  setOperatorPromptDismissed: (v: boolean) => void;
  setAuditorPromptDismissed: (v: boolean) => void;
  setAnalystPromptDismissed: (v: boolean) => void;
  setPendingAuditCount: (n: number) => void;
  setPendingCollabMentionCount: (n: number) => void;
  setActivityId: (id: UIState['activityId']) => void;
  setLeftPanelMode: (m: UIState['leftPanelMode']) => void;
  setDevPanelMode: (m: DevPanelMode) => void;
  setRightPanelWidth: (w: number) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      commandPaletteOpen: false,
      quickOpenOpen: false,
      cheatSheetOpen: false,
      agentStatus: 'unknown',
      errorCount: 0,
      warnCount: 0,
      cursorLine: 1,
      cursorCol: 1,
      editorSplit: null,
      secondaryTabId: null,
      mode: 'full',
      xtermDrawerOpen: false,
      operatorModePromptDismissed: false,
      auditorModePromptDismissed: false,
      analystModePromptDismissed: false,
      pendingAuditCount: 0,
      pendingCollabMentionCount: 0,
      activityId: 'explorer',
      leftPanelMode: 'auto',
      devPanelMode: 'assets',
      rightPanelWidth: 320,

      toggleCommandPalette: (open) =>
        set((s) => ({ commandPaletteOpen: open ?? !s.commandPaletteOpen })),
      toggleQuickOpen: (open) =>
        set((s) => ({ quickOpenOpen: open ?? !s.quickOpenOpen })),
      toggleCheatSheet: (open) =>
        set((s) => ({ cheatSheetOpen: open ?? !s.cheatSheetOpen })),
      setAgentStatus: (s) => set({ agentStatus: s }),
      setErrorCount: (n) => set({ errorCount: n }),
      setWarnCount: (n) => set({ warnCount: n }),
      setCursor: (line, col) => set({ cursorLine: line, cursorCol: col }),
      setEditorSplit: (m) =>
        set({
          editorSplit: m,
          secondaryTabId: m === null ? null : null,
        }),
      setSecondaryTabId: (id) => set({ secondaryTabId: id }),
      setMode: (m) =>
        set({
          mode: m,
          // 切 mode 时同步重置 ActivityBar 到 explorer，避免 collab 状态跨 mode 残留
          // 路由跳转由 ModeSwitcher 显式 navigate('/') 完成（store 不知道 router）
          activityId: 'explorer',
        }),
      toggleXtermDrawer: (open) =>
        set((s) => ({ xtermDrawerOpen: open ?? !s.xtermDrawerOpen })),
      setOperatorPromptDismissed: (v) => set({ operatorModePromptDismissed: v }),
      setAuditorPromptDismissed: (v) => set({ auditorModePromptDismissed: v }),
      setAnalystPromptDismissed: (v) => set({ analystModePromptDismissed: v }),
      setPendingAuditCount: (n) => set({ pendingAuditCount: n }),
      setPendingCollabMentionCount: (n) => set({ pendingCollabMentionCount: n }),
      setActivityId: (id) => set({ activityId: id }),
      setLeftPanelMode: (m) => set({ leftPanelMode: m }),
      setDevPanelMode: (m) => set({ devPanelMode: m }),
      setRightPanelWidth: (w) => set({ rightPanelWidth: Math.min(720, Math.max(240, w)) }),
    }),
    {
      name: 'eaide.ui',
      storage: createJSONStorage(() => localStorage),
      // 持久化：mode（保留用户上次选择）+ 弹窗偏好
      // 首次安装 localStorage 为空 → 走初始 state 默认值 'full'
      // 后续启动会读回用户上次关闭时的 mode
      // ★ onRehydrateStorage 迁移：旧 'audit' → 'auditor'（Phase 5 改名）
      partialize: (s) => ({
        mode: s.mode,
        operatorModePromptDismissed: s.operatorModePromptDismissed,
        auditorModePromptDismissed: s.auditorModePromptDismissed,
        analystModePromptDismissed: s.analystModePromptDismissed,
        leftPanelMode: s.leftPanelMode,
        devPanelMode: s.devPanelMode,
        rightPanelWidth: s.rightPanelWidth,
      }),
      onRehydrateStorage: () => (state) => {
        // 旧版 'audit' 占位模式 → 新版 'auditor'（Phase 5 改名）
        if (state && (state as { mode?: string }).mode === 'audit') {
          state.mode = 'auditor';
        }
        // 注：'operator' 是独立顶级页签（与开发模式并列），不做迁移
        // Phase 2H：leftPanelMode 旧值 → devPanelMode（'system'→assets，'business'→features，auto→assets）
        const st = state as {
          leftPanelMode?: string;
          devPanelMode?: DevPanelMode;
        } | null;
        if (st && !st.devPanelMode) {
          if (st.leftPanelMode === 'business') st.devPanelMode = 'features';
          else st.devPanelMode = 'assets';
        }
      },
    },
  ),
);
