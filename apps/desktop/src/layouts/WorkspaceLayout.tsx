/**
 * WorkspaceLayout —— VSCode 风格的 IDE 整体框架。
 *
 *  ┌──────────────────────────────────────────────────────────────────┐
 *  │ MenuBar (File Edit View …)                                        │
 *  ├──────────────────────────────────────────────────────────────────┤
 *  │ TopBar (醒目：ENV 徽章  │ 模式切换 │ Agent 状态)                  │
 *  ├──┬──────────┬─────────────────────────────┬─────────────────────┤
 *  │  │          │                             │                     │
 *  │A │ Side Bar │   Center (Editor + Tabs)    │   Right Panel       │
 *  │c │  (资产)  │   (对话 + 代码块 + Monaco)   │   (执行链路)        │
 *  │t │          │                             │                     │
 *  │i ├──────────┴────────────────────────────┤                     │
 *  │t │  BottomPanel (Xterm 日志 / 5 tab)         │                     │
 *  │v ├────────────────────────────────────────────────────────────────┤
 *  │  │ StatusBar                                                    │
 *  └──┴────────────────────────────────────────────────────────────────┘
 *
 * Phase 2A + 2E 改造：
 *   - 4 行 grid：MenuBar (auto) / TopBar (auto) / Main (1fr) / StatusBar (auto)
 *   - Main 内部用 flex：SideBar | Center(含 Bottom) | Right
 *   - Phase 2E operator 模式：通过 [data-mode] CSS 隐藏 Bottom + Right（220ms 过渡）
 *   - 状态保留：隐藏的面板不卸载，只 height/width 0
 */
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { ActivityBar } from '@/components/chrome/ActivityBar';
import { type ActivityId } from '@/store/uiStore';
import { MenuBar } from '@/components/chrome/MenuBar';
import { TopBar } from '@/components/chrome/TopBar';
import { StatusBar } from '@/components/chrome/StatusBar';
import { CheatSheet } from '@/components/chrome/CheatSheet';
import { CommandPalette } from '@/components/chrome/CommandPalette';
import { QuickOpen } from '@/components/chrome/QuickOpen';
import { FindInFiles } from '@/components/asset-tree/FindInFiles';
import { RightTraceView } from './RightTraceView';
import { ProjectFileTree } from '@/components/codenav/ProjectFileTree';
import { SystemAssetTree } from '@/components/asset-tree/SystemAssetTree';
import { XtermDrawer } from './XtermDrawer';
import { AuditDashboard } from '@/views/AuditDashboard';
import { DataWorkbench } from '@/views/DataWorkbench';
import { CollabCenter } from '@/views/CollabCenter';
// reqflow V1：需求工作台（运营专家需求卡片管理）
import { ReqWorkbenchView } from '@/views/ReqWorkbenchView';
import { BusinessFeatureTree } from '@/components/asset-tree/BusinessFeatureTree';
// 左侧「任务计划」面板（2026-08-28）：与资源管理器并列可切，竖向进度卡常驻展示
import { LeftTaskPlanPanel } from '@/components/chat/LeftTaskPlanPanel';
// Phase 6 V1.5：会话管理侧栏（穿透所有 mode；FTS5 搜索 + 会话列表 + 分支/共享/导出/恢复入口）
import { SessionsPanel } from '@/components/sessions/SessionsPanel';
// BUGFIX #67 (2026-08-13)：会话内嵌浏览视图（替换 <Outlet />；之前 router 没注册
// /sessions/:id 子路由导致 center 区域始终显示 HomeView 欢迎页）
import { SessionInlineView } from '@/components/sessions/SessionInlineView';
// Phase 6 V1.6 (2026-08-06)：启动恢复面板 + SSE 订阅（压缩/记忆蒸馏事件）
import { RecoveryPanel } from '@/components/sessions/RecoveryPanel';
import { useSessionsStore } from '@/store/sessionsStore';
import { useSkillsStore } from '@/store/skillsStore';
import { useChatStore } from '@/store/chatStore';
import { useExpertTeamStore } from '@/store/expertTeamStore';
import { DataSourceTree } from '@/components/data/DataSourceTree';
import { DataDictionaryPanel } from '@/components/ops/DataDictionaryPanel';
import { LeftPanelDevSwitcher } from '@/components/asset-tree/LeftPanelDevSwitcher';
import { useLeftPanelContent } from '@/store/leftPanel';
import { BiznavChatBridge } from '@/components/biznav/BiznavChatBridge';
import { FeatureDetailPanel } from '@/components/biznav/FeatureDetailPanel';
import { FeatureEditorModal } from '@/components/biznav/FeatureEditorModal';
import { SkillEditorModal } from '@/components/skills/SkillEditorModal';
import { SkillImportDialog } from '@/components/skills/SkillImportDialog';
import { OperationsWorkbench } from '@/views/OperationsWorkbench';
import { ReqCardsRightPanel } from '@/components/reqflow/ReqCardsRightPanel';
import { useUIStore, type WorkMode } from '@/store/uiStore';
import { startPushMock, stopPushMock } from '@/lib/pushMock';

export function WorkspaceLayout(): JSX.Element {
  // activityId 提升到 uiStore：setMode 时自动重置（避免跨 mode 残留）
  // 保留本地 useActivityBar 作为兜底（防止 uiStore 持久化版本缺字段导致 undefined）
  const storedActive = useUIStore((s) => s.activityId);
  const setActive = useUIStore((s) => s.setActivityId);
  const active: ActivityId = storedActive ?? 'explorer';
  const mode = useUIStore((s) => s.mode);
  const xtermDrawerOpen = useUIStore((s) => s.xtermDrawerOpen);
  const navigate = useNavigate();
  const location = useLocation();
  // BUGFIX #66：设置页穿透所有模式——无论 operator/auditor/analyst 是否接管 center，
  // 只要 URL 在 /settings 下就渲染 Outlet（SettingsView），避免点设置没反应
  const settingsOpen = location.pathname.startsWith('/settings');

  // Phase 2E + Phase 9：mode 变化时同步 navigate('/') 清掉 settings URL
  // 解决 bug：用户在 /settings/models 切到 operator 模式时，SettingsView 仍占据 center 区域
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const path = window.location.pathname;
    if (path.startsWith('/settings')) {
      navigate('/', { replace: true });
    }
  }, [mode, navigate]);

  // 模式隔离（2026-08-11）：切模式时同步切到该模式的对话页签组，
  // 避免专家团模式的对话串进开发模式（全局单一 activeTabId 的历史缺陷）
  useEffect(() => {
    useChatStore.getState().ensureModeTab(mode);
  }, [mode]);

  // 全局快捷键：Ctrl+~ 切换 Xterm 抽屉（Phase 2E 逃生通道）
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.ctrlKey && e.key === '`') {
        e.preventDefault();
        useUIStore.getState().toggleXtermDrawer();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Phase 2H：启动即加载 Skill 列表（功能点绑定 Skill 的注入依赖该列表）
  // 专家团资产同步加载（运营工作台自动选择 + 设置页维护依赖）
  useEffect(() => {
    void useSkillsStore.getState().loadSkills();
    void useExpertTeamStore.getState().loadTeams();
  }, []);

  // Phase 9 协作推送 mock：用户在 ActivityBar 选中 collab 时启动
  // 离开 / 卸载时清理（避免 HMR 泄漏）
  useEffect(() => {
    if (active === 'collab') {
      startPushMock();
      return () => stopPushMock();
    }
    return undefined;
  }, [active]);

  // Phase 6 V1.6：会话恢复扫描 + SSE 事件订阅
  // 启动后扫描中断会话（空闲 > 5min），needs_recovery=true 时弹 RecoveryPanel；
  // 同时订阅 session_compression_applied / session_memory_consolidated SSE 事件。
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  useEffect(() => {
    let mounted = true;
    void useSessionsStore
      .getState()
      .loadRecovery({ idle_threshold_ms: 300_000, limit: 50 })
      .then((report) => {
        if (mounted && report?.needs_recovery && report.total > 0) {
          setRecoveryOpen(true);
        }
      });
    const unsubscribe = useSessionsStore.getState().subscribeSSE();
    return () => {
      mounted = false;
      unsubscribe();
    };
  }, []);

  return (
    <div
      className="workspace-root grid h-screen w-screen"
      data-mode={mode}
      data-activity={active}
      style={{
        // 4 行：MenuBar / TopBar / Main / StatusBar
        gridTemplateRows: 'auto auto 1fr auto',
        gridTemplateColumns: '48px 1fr',
        backgroundColor: '#ffffff',
        color: '#1f1f1f',
      }}
    >
      {/* Phase 2G: headless 桥接 biznavStore → chatStore（spec §2.3）— 杜绝 React #300 */}
      <BiznavChatBridge />
      {/* Phase 2G: 功能点编辑器（800×600 Modal，跨 mode 可见） */}
      <FeatureEditorModal />
      {/* Phase 2D V0: Skill 编辑器 + 导入对话框（800×600 / 对话框，跨 mode 可见） */}
      <SkillEditorModal />
      <SkillImportDialog />
      {/* Phase 2G: 功能点详情（360px 右抽屉，仅 operator 模式挂载） */}
      {/* Row 1: MenuBar（跨全宽） */}
      <div style={{ gridColumn: '1 / -1' }}>
        <MenuBar />
      </div>

      {/* Row 2: TopBar（醒目：ENV + 模式切换 + Agent 状态） */}
      <div style={{ gridColumn: '1 / -1' }}>
        <TopBar />
      </div>

      {/* Row 3: Main 区域 —— ActivityBar / SideBar / Center+Bottom / RightPanel */}
      {/* 2026-07-15 撤销 BUGFIX #17 的 auditor 隐藏（用户要求保留最左侧导航栏） */}
      <ActivityBar active={active} onChange={(id) => setActive(id)} />

      <div
        className="workspace-main flex overflow-hidden"
        style={{ gridColumn: '2 / 3' }}
      >
        <SideBar activity={active} mode={mode} />
        <main
          className="workspace-center flex flex-1 flex-col overflow-hidden"
          style={{ backgroundColor: '#ffffff', borderLeft: '1px solid #d0d0d0' }}
        >
          <section className="flex-1 overflow-hidden">
            {/* 设置页穿透所有模式（BUGFIX #66）；
                横切 activity（协作/需求/代码符号/会话管理）优先于专家模式分支，
                保证左侧按钮在任何模式下都能覆盖当前工作台（用户要求 2026-08-07） */}
            {settingsOpen ? (
              <Outlet />
            ) : active === 'collab' ? (
              <CollabCenter />
            ) : active === 'requirements' ? (
              // reqflow V1：需求工作台（运营专家需求卡片管理，三栏布局）
              <ReqWorkbenchView />
            ) : active === 'code-nav' ? (
              // Phase 2F V0 收尾 (2026-07-28)：代码符号搜索顶级入口全屏展示
              // SideBar 由 SideBar() 函数 early-return 折叠（让位 320px 双栏）
              <div className="h-full">
                <FindInFiles defaultMode="symbol" />
              </div>
            ) : active === 'sessions' ? (
              // BUGFIX #67：内嵌会话详情视图（之前 <Outlet /> 在 router 没匹配子路由，
              // 回落到 HomeView → chatStore 没该会话 tab → 显示欢迎页。
              // 修复后：SessionsPanel.onClick 设置 activeSessionId → 本组件订阅拉详情）
              <SessionInlineView />
            ) : mode === 'auditor' ? (
              <AuditDashboard />
            ) : mode === 'analyst' ? (
              <DataWorkbench />
            ) : mode === 'operator' ? (
              // Phase 2H 收尾（用户要求）：运营模式是独立顶级页签（与开发模式并列），
              // 全屏接管：业务列表 + Chat + 工作台（SideBar 由 [data-mode] CSS 折叠）
              <OperationsWorkbench />
            ) : (
              <Outlet />
            )}
          </section>
          {/* Phase 12 V1：底部 Xterm 终端已并入主对话（Codex/Claude 风格内联 log），
              不再单独占一栏。需要看完整日志时点「ChatInput 旁的 ⌨ 终端」开关抽屉。 */}
          {/* 旧 BottomTerminal 已并入 ExecutionBlock — 保留 section 占位让以后回滚 */}
          <div style={{ display: 'none' }} />
        </main>
        {/* 审核/数据专家模式不需要右侧 Trace 面板（独立布局） */}
        {mode !== 'auditor' && mode !== 'analyst' && mode !== 'operator' && (
          <RightPanel />
        )}
        {/* Phase 2G: 功能点详情（开发 / 运营模式都挂载） */}
        {(mode === 'full' || mode === 'operator') && (
          <FeatureDetailPanel />
        )}
      </div>

      {/* Row 4: StatusBar（跨全宽） */}
      <div style={{ gridColumn: '1 / -1' }}>
        <StatusBar />
      </div>

      {/* 全局弹窗 */}
      <CommandPalette />
      <QuickOpen />
      <CheatSheet />

      {/* Phase 2E 逃生通道：Ctrl+~ 半屏 Xterm 抽屉 */}
      <XtermDrawer
        open={xtermDrawerOpen}
        onClose={() => useUIStore.getState().toggleXtermDrawer(false)}
      />

      {/* Phase 6 V1.6：启动恢复面板（检测到中断会话时弹出；恢复 = 选中会话并切到会话侧栏） */}
      <RecoveryPanel
        open={recoveryOpen}
        onClose={() => setRecoveryOpen(false)}
        onResume={(sid) => {
          useSessionsStore.getState().setActive(sid);
          void useSessionsStore.getState().get(sid);
          setActive('sessions');
        }}
      />

      {/* 全局 CSS：模式切换的丝滑过渡 */}
      <style>{WORKSPACE_TRANSITION_CSS}</style>
    </div>
  );
}

/**
 * 全局 CSS：
 *   - 完整 IDE 模式：BottomPanel 高度 240px，RightPanel 宽度 320px
 *   - 运营专家模式：BottomPanel 高度 0 + RightPanel 宽度 0（隐藏但不卸载）
 *   - 过渡 220ms ease-out
 */
const WORKSPACE_TRANSITION_CSS = `
  .workspace-bottom {
    transition: height 220ms cubic-bezier(0.4, 0, 0.2, 1),
                opacity 180ms ease-out;
  }
  [data-mode="operator"] .workspace-bottom,
  [data-mode="auditor"] .workspace-bottom,
  [data-mode="analyst"] .workspace-bottom {
    height: 0 !important;
    opacity: 0;
    pointer-events: none;
  }

  /* 专家模式自带左栏（运营/审核/数据），折叠外层 SideBar；
     但横切 activity（sessions/search）选中时保持可见（跨全模式，用户要求 2026-08-07） */
  [data-mode="analyst"]:not([data-activity="sessions"]):not([data-activity="search"]) .workspace-sidebar,
  [data-mode="operator"]:not([data-activity="sessions"]):not([data-activity="search"]) .workspace-sidebar,
  [data-mode="auditor"]:not([data-activity="sessions"]):not([data-activity="search"]) .workspace-sidebar {
    width: 0 !important;
    opacity: 0;
    pointer-events: none;
  }

  /* 数据字典是独立顶级入口：选中时即使在 operator/auditor/analyst 全接管模式下
     也保持 SideBar 展开（覆盖下方各模式的折叠规则） */
  .workspace-root:has(.data-dict-active) .workspace-sidebar {
    width: 260px !important;
    opacity: 1 !important;
    pointer-events: auto !important;
  }

  /* Phase 9 协作中心：ActivityBar 选中 collab 时折叠外层 SideBar，center 区域全屏 */
  .workspace-root:has(.collab-active) .workspace-sidebar {
    width: 0 !important;
    opacity: 0;
    pointer-events: none;
  }

  .workspace-main {
    height: 100%;
    min-height: 0;
  }

  .workspace-center {
    min-width: 0;
  }
`;

// ---- SideBar：左侧面板（始终显示，由 ActivityBar 控制内容） -------------

function SideBar({ activity, mode }: { activity: ActivityId; mode: WorkMode }): JSX.Element {
  // 左侧面板宽度可拖拽（2026-08-28）：同右侧控制台面板的 sash 方案
  const panelWidth = useUIStore((s) => s.leftPanelWidth);
  const setPanelWidth = useUIStore((s) => s.setLeftPanelWidth);
  const leftView = useUIStore((s) => s.leftView);
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const onSashDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    dragRef.current = { startX: e.clientX, startW: panelWidth };
    setDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onSashMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    const d = dragRef.current;
    if (!d) return;
    // 面板靠左：向右拖（clientX - startX > 0）变宽
    setPanelWidth(d.startW + (e.clientX - d.startX));
  };
  const onSashUp = (): void => {
    dragRef.current = null;
    setDragging(false);
  };

  // Phase 2F V0 收尾 (2026-07-28)：code-nav 全屏渲染 FindInFiles symbol 模式
  // SideBar 折叠让位 320px 双栏（用 JS early-return 而非 CSS :has，避免 ActivityBar 切换瞬间 SideBar 闪烁）。
  // 这与 collab 的 CSS :has 方案不同 —— V1 应统一为一种模式。
  if (activity === 'code-nav') {
    return (
      <aside
        className="workspace-sidebar"
        style={{ width: 0, borderRight: 'none', pointerEvents: 'none' }}
        aria-hidden
      />
    );
  }
  // reqflow V1：需求工作台全屏渲染，SideBar 折叠让位（同 code-nav）
  if (activity === 'requirements') {
    return (
      <aside
        className="workspace-sidebar"
        style={{ width: 0, borderRight: 'none', pointerEvents: 'none' }}
        aria-hidden
      />
    );
  }
  // 任务计划视图生效范围：仅开发模式 + explorer activity（其它 activity 各有专属内容）
  const showPlan = mode === 'full' && activity === 'explorer' && leftView === 'plan';
  return (
    <aside
      className="workspace-sidebar relative flex flex-col overflow-hidden"
      style={{
        width: panelWidth,
        backgroundColor: '#f3f3f3',
        borderRight: '1px solid #e0e0e0',
        transition: dragging
          ? 'none'
          : 'width 220ms cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      {/* 拖拽条：右缘 5px，hover 高亮；双击恢复默认 260 */}
      <div
        role="separator"
        aria-orientation="vertical"
        title="拖动调节宽度，双击恢复默认"
        onPointerDown={onSashDown}
        onPointerMove={onSashMove}
        onPointerUp={onSashUp}
        onDoubleClick={() => setPanelWidth(260)}
        className="absolute right-0 top-0 z-10 h-full w-[5px] hover:bg-[#0078d4]/40"
        style={{ cursor: 'col-resize', backgroundColor: dragging ? 'rgba(0,120,212,0.4)' : 'transparent' }}
      />
      <SideBarHeader activity={activity} mode={mode} />
      {/* Phase 2H：开发模式下显示 系统资产/文件列表/系统功能点 三态子切换；
          会话管理/数据字典等自带专属内容的 activity 不显示（用户要求 2026-08-07）；
          任务计划视图下隐藏（计划面板自带全高内容区） */}
      {mode === 'full' && !showPlan && activity !== 'sessions' && activity !== 'search' && activity !== 'collab' && activity !== 'data-dict' && (
        <LeftPanelDevSwitcher />
      )}
      <div className="flex-1 overflow-auto">
        {showPlan ? (
          // 任务计划（2026-08-28）：竖向进度卡常驻左侧，不随对话区滚动丢失
          <LeftTaskPlanPanel />
        ) : activity === 'collab' ? (
          // Phase 9 协作中心：center 区域已全屏渲染，SideBar 折叠（CSS）
          <PlaceholderPanel title="协作中心" hint="主区域全屏展示，点击 ActivityBar 其他图标切换" />
        ) : activity === 'search' ? (
          // Phase 2F：search activity 穿透所有 mode（搜索是横切能力，不被 mode 覆盖）
          <FindInFiles />
        ) : activity === 'sessions' ? (
          // Phase 6 V1.5：会话管理侧栏（穿透所有 mode；FTS5 搜索 + 会话列表）
          <SessionsPanel />
        ) : activity === 'data-dict' ? (
          // 数据字典：独立侧栏入口（穿透所有 mode，原运营工作台右侧 tab 迁出）
          <DataDictionaryPanel />
        ) : mode === 'analyst' ? (
          // 数据专家模式：默认 DataSourceTree（外层 SideBar 折叠由 DataWorkbench 内部管理）
          <DataSourceTree />
        ) : mode === 'auditor' ? (
          // 审核专家模式：占位
          <PlaceholderPanel title="审核工作台" hint="功能开发中" />
        ) : (
          // 'full' / 'operator'：按 leftPanelContent 决定
          <LeftPanelBody activity={activity} />
        )}
      </div>
    </aside>
  );
}

/**
 * Phase 2G V1.3：根据 useLeftPanelContent() 渲染左侧栏主体。
 * 'system'   → SystemAssetTree / 各 activity 占位（'full' 模式默认）
 * 'business' → BusinessFeatureTree（运营视角）
 */
function LeftPanelBody({ activity }: { activity: ActivityId }): JSX.Element {
  const content = useLeftPanelContent();
  // Phase 2H：文件列表（工程目录树，界面不变）
  if (content === 'files') {
    return <ProjectFileTree />;
  }
  if (content === 'business') {
    return <BusinessFeatureTree />;
  }
  // 'system'
  return (
    <>
      {/* Phase 2H：系统资产 Tab 只显示资产树（文件列表是独立 Tab） */}
      {activity === 'explorer' && <SystemAssetTree />}
      {activity === 'source-control' && (
        <PlaceholderPanel title="审计 / 版本" hint="SQL / API 调用历史 + 审计行（占位）" />
      )}
      {activity === 'run-debug' && <PlaceholderPanel title="运行 / 调试" hint="LangGraph 节点调试器（占位）" />}
      {activity === 'extensions' && <PlaceholderPanel title="扩展" hint="MCP 插件市场（占位）" />}
    </>
  );
}

function SideBarHeader({
  activity,
  mode,
}: {
  activity: ActivityId;
  mode: WorkMode;
}): JSX.Element {
  // CRITICAL fix (2026-08-07，仿 BUGFIX #15)：hook 必须无条件调用。
  // 旧实现把 useLeftPanelContent() 写在三元表达式里，仅 mode==='full' 时才调，
  // 切换左侧面板（系统资产/文件列表/系统功能点）时 hook 数量变化 → React #300。
  const content = useLeftPanelContent();
  const leftView = useUIStore((s) => s.leftView);
  const setLeftView = useUIStore((s) => s.setLeftView);
  // 资源管理器 / 任务计划 并列切换（2026-08-28）：仅开发模式 + explorer activity
  const switchable = mode === 'full' && activity === 'explorer';
  // Phase 2H：开发模式标题跟随 devPanelMode；但 sessions/search/collab 等自带专属内容的
  // activity 始终用 TITLES（否则会话管理会显示成「文件列表/系统功能点」，用户要求 2026-08-07）
  const title =
    mode === 'full' && activity !== 'sessions' && activity !== 'search' && activity !== 'collab' && activity !== 'data-dict'
      ? content === 'files'
        ? '文件列表'
        : content === 'business'
          ? '系统功能点'
          : TITLES[activity]
      : TITLES[activity];
  if (switchable) {
    return (
      <div
        className="flex h-[35px] items-stretch border-b text-ui font-semibold uppercase tracking-wide"
        style={{ borderColor: '#e0e0e0', color: '#333333' }}
      >
        {([
          ['explorer', TITLES.explorer],
          ['plan', '任务计划'],
        ] as Array<['explorer' | 'plan', string]>).map(([view, label]) => {
          const activeView = leftView === view;
          return (
            <button
              key={view}
              type="button"
              onClick={() => setLeftView(view)}
              className="flex-1 border-b-2 px-2 transition-colors"
              style={{
                borderColor: activeView ? '#0078d4' : 'transparent',
                color: activeView ? '#0078d4' : '#6e6e6e',
                backgroundColor: activeView ? '#ffffff' : 'transparent',
              }}
              title={label}
            >
              {label}
            </button>
          );
        })}
      </div>
    );
  }
  return (
    <div
      className="flex h-[35px] items-center justify-between border-b px-4 text-ui font-semibold uppercase tracking-wide"
      style={{ borderColor: '#e0e0e0', color: '#333333' }}
    >
      <span>{title}</span>
    </div>
  );
}

const TITLES: Record<ActivityId, string> = {
  explorer: '资源管理器',
  search: '搜索',
  'source-control': '源代码管理',
  'run-debug': '运行和调试',
  extensions: '扩展',
  collab: '协作中心',
  // Phase 2F V0 收尾 (2026-07-28)：代码符号搜索顶级入口
  // 此条目永远不会渲染为 SideBar 标题（code-nav 在 SideBar() 中 early-return），
  // 仅用于满足 Record<ActivityId, string> 类型完备性。
  'code-nav': '代码符号',
  // Phase 6 V1.5 (2026-07-31)：会话管理顶级入口
  sessions: '会话管理',
  // reqflow V1 (2026-08-05)：需求工作台（同 code-nav，SideBar early-return 让位）
  requirements: '需求工作台',
  // 数据字典：独立顶级入口（原运营工作台右侧 tab 迁出）
  'data-dict': '数据字典',
};

function PlaceholderPanel({ title, hint }: { title: string; hint: string }): JSX.Element {
  return (
    <div className="flex h-full flex-col items-center justify-center p-6 text-center text-fg-muted">
      <div className="mb-2 text-ui font-semibold text-fg">{title}</div>
      <div className="text-2xs">{hint}</div>
    </div>
  );
}

// ---- RightPanel：控制台（执行链路 + AI 解释）；operator 模式下走隐藏 + 过渡 -------------------

function RightPanel(): JSX.Element {
  const mode = useUIStore((s) => s.mode);
  const devPanelMode = useUIStore((s) => s.devPanelMode);
  const panelWidth = useUIStore((s) => s.rightPanelWidth);
  const setPanelWidth = useUIStore((s) => s.setRightPanelWidth);
  // 仅 'full' (开发模式) 显示；'auditor' 隐藏
  // 'auditor' 模式有自己独立的三栏布局（Phase 5）
  const visible = mode === 'full';
  // Phase 2H：系统功能点子模式右侧为需求卡片（默认更宽），其余保持思维链现状
  const featuresMode = mode === 'full' && devPanelMode === 'features';
  const contentWidth = featuresMode ? Math.max(panelWidth, 380) : panelWidth;

  // 拖拽调节宽度（VSCode 风格 sash：按住左缘拖动，指针捕获避免拖出边界）
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const onSashDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    dragRef.current = { startX: e.clientX, startW: panelWidth };
    setDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onSashMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    const d = dragRef.current;
    if (!d) return;
    // 面板靠右：向左拖（startX - clientX > 0）变宽
    setPanelWidth(d.startW + (d.startX - e.clientX));
  };
  const onSashUp = (): void => {
    dragRef.current = null;
    setDragging(false);
  };

  return (
    <aside
      className="workspace-right relative flex flex-col overflow-hidden"
      style={{
        width: visible ? contentWidth : 0,
        backgroundColor: '#f3f3f3',
        borderLeft: '1px solid #e0e0e0',
        opacity: visible ? 1 : 0,
        transition: dragging
          ? 'none'
          : 'width 220ms cubic-bezier(0.4, 0, 0.2, 1), opacity 180ms ease-out',
        pointerEvents: visible ? 'auto' : 'none',
      }}
    >
      {/* 拖拽条：左缘 5px，hover 高亮；双击恢复默认 320 */}
      {visible && (
        <div
          role="separator"
          aria-orientation="vertical"
          title="拖动调节宽度，双击恢复默认"
          onPointerDown={onSashDown}
          onPointerMove={onSashMove}
          onPointerUp={onSashUp}
          onDoubleClick={() => setPanelWidth(320)}
          className="absolute left-0 top-0 z-10 h-full w-[5px] hover:bg-[#0078d4]/40"
          style={{ cursor: 'col-resize', backgroundColor: dragging ? 'rgba(0,120,212,0.4)' : 'transparent' }}
        />
      )}
      <div
        className="flex h-[35px] items-center border-b px-4 text-ui font-semibold uppercase tracking-wide"
        style={{ borderColor: '#e0e0e0', color: '#333333' }}
      >
        <span>{featuresMode ? '📋 需求卡片' : '控制台'}</span>
      </div>
      {/* 内容区允许选中复制（覆盖 workspace-root 的 select-none） */}
      <div className="flex-1 select-text overflow-auto">
        {featuresMode ? <ReqCardsRightPanel /> : <RightTraceView />}
      </div>
    </aside>
  );
}
