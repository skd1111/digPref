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
import { useEffect } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { ActivityBar } from '@/components/chrome/ActivityBar';
import { type ActivityId } from '@/store/uiStore';
import { MenuBar } from '@/components/chrome/MenuBar';
import { TopBar } from '@/components/chrome/TopBar';
import { StatusBar } from '@/components/chrome/StatusBar';
import { CheatSheet } from '@/components/chrome/CheatSheet';
import { CommandPalette } from '@/components/chrome/CommandPalette';
import { QuickOpen } from '@/components/chrome/QuickOpen';
import { FindInFiles } from '@/components/asset-tree/FindInFiles';
import { LeftAssetTree } from './LeftAssetTree';
import { RightTraceView } from './RightTraceView';
import { XtermDrawer } from './XtermDrawer';
import { AuditDashboard } from '@/views/AuditDashboard';
import { DataWorkbench } from '@/views/DataWorkbench';
import { CollabCenter } from '@/views/CollabCenter';
import { BusinessFeatureTree } from '@/components/asset-tree/BusinessFeatureTree';
// Phase 6 V1.5：会话管理侧栏（穿透所有 mode；FTS5 搜索 + 会话列表 + 分支/共享/导出/恢复入口）
import { SessionsPanel } from '@/components/sessions/SessionsPanel';
import { DataSourceTree } from '@/components/data/DataSourceTree';
import { LeftPanelModeToggle } from '@/components/asset-tree/LeftPanelModeToggle';
import { useLeftPanelContent } from '@/store/leftPanel';
import { BiznavChatBridge } from '@/components/biznav/BiznavChatBridge';
import { FeatureDetailPanel } from '@/components/biznav/FeatureDetailPanel';
import { FeatureEditorModal } from '@/components/biznav/FeatureEditorModal';
import { SkillEditorModal } from '@/components/skills/SkillEditorModal';
import { SkillImportDialog } from '@/components/skills/SkillImportDialog';
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

  // Phase 2E + Phase 9：mode 变化时同步 navigate('/') 清掉 settings URL
  // 解决 bug：用户在 /settings/models 切到 operator 模式时，SettingsView 仍占据 center 区域
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const path = window.location.pathname;
    if (path.startsWith('/settings')) {
      navigate('/', { replace: true });
    }
  }, [mode, navigate]);

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

  // Phase 9 协作推送 mock：用户在 ActivityBar 选中 collab 时启动
  // 离开 / 卸载时清理（避免 HMR 泄漏）
  useEffect(() => {
    if (active === 'collab') {
      startPushMock();
      return () => stopPushMock();
    }
    return undefined;
  }, [active]);

  return (
    <div
      className="workspace-root grid h-screen w-screen select-none"
      data-mode={mode}
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
            {/* 专家模式渲染各自独立布局，隐藏 SideBar/Bottom/Right */}
            {mode === 'auditor' ? (
              <AuditDashboard />
            ) : mode === 'analyst' ? (
              <DataWorkbench />
            ) : active === 'collab' ? (
              <CollabCenter />
            ) : active === 'code-nav' ? (
              // Phase 2F V0 收尾 (2026-07-28)：代码符号搜索顶级入口全屏展示
              // SideBar 由 SideBar() 函数 early-return 折叠（让位 320px 双栏）
              // ⚠ auditor / analyst 模式优先级高于 activity 分支 —— 在这两个模式下点 ⌘ 不会切换视图。
              //    这与 collab 行为一致（auditor/analyst 是全接管模式）。V1 可考虑灰掉图标或加 tooltip。
              <div className="h-full">
                <FindInFiles defaultMode="symbol" />
              </div>
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
        {mode !== 'auditor' && mode !== 'analyst' && <RightPanel />}
        {/* Phase 2G: 功能点详情（仅 operator 模式挂载） */}
        {mode === 'operator' && <FeatureDetailPanel />}
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

  /* 数据专家模式有自带左栏（数据源树），折叠外层 SideBar 避免重复 */
  [data-mode="analyst"] .workspace-sidebar {
    width: 0 !important;
    opacity: 0;
    pointer-events: none;
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
  return (
    <aside
      className="workspace-sidebar flex flex-col overflow-hidden"
      style={{
        width: 260,
        backgroundColor: '#f3f3f3',
        borderRight: '1px solid #e0e0e0',
        transition: 'width 220ms cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      <SideBarHeader activity={activity} mode={mode} />
      <div className="flex-1 overflow-auto">
        {/* Phase 9 协作中心：center 区域已全屏渲染，SideBar 折叠（CSS） */}
        {activity === 'collab' ? (
          <PlaceholderPanel title="协作中心" hint="主区域全屏展示，点击 ActivityBar 其他图标切换" />
        ) : activity === 'search' ? (
          // Phase 2F：search activity 穿透所有 mode（搜索是横切能力，不被 mode 覆盖）
          <FindInFiles />
        ) : activity === 'sessions' ? (
          // Phase 6 V1.5：会话管理侧栏（穿透所有 mode；FTS5 搜索 + 会话列表）
          <SessionsPanel />
        ) : mode === 'analyst' ? (
          // 数据专家模式：默认 DataSourceTree（外层 SideBar 折叠由 DataWorkbench 内部管理）
          <DataSourceTree />
        ) : mode === 'auditor' ? (
          // 审核专家模式：占位（Phase 5 实现）
          <PlaceholderPanel title="审核工作台" hint="Phase 5 占位" />
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
  if (content === 'business') {
    return <BusinessFeatureTree />;
  }
  // 'system'
  return (
    <>
      {activity === 'explorer' && <LeftAssetTree />}
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
  // 运营专家模式强制显示 "业务功能点" 标题（V0 行为；V1.3 由 useLeftPanelContent 接管显示内容）
  const title = mode === 'operator' ? '业务功能点' : TITLES[activity];
  // V1.3 仅在 mode 允许切换的场景（'full' / 'operator'）显示 toggle 按钮
  // analyst / auditor 模式左侧栏是独立组件（DataSourceTree / AuditDashboard），
  // 没有"业务/系统资产"切换语义。
  const showToggle = mode === 'full' || mode === 'operator';
  return (
    <div
      className="flex h-[35px] items-center justify-between border-b px-4 text-ui font-semibold uppercase tracking-wide"
      style={{ borderColor: '#e0e0e0', color: '#333333' }}
    >
      <span>{title}</span>
      {showToggle && <LeftPanelModeToggle />}
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
  // 仅 'full' (开发模式) 显示；'operator' / 'auditor' 隐藏
  // 'auditor' 模式有自己独立的三栏布局（Phase 5）
  const visible = mode === 'full';

  return (
    <aside
      className="workspace-right flex flex-col overflow-hidden"
      style={{
        width: visible ? 320 : 0,
        backgroundColor: '#f3f3f3',
        borderLeft: '1px solid #e0e0e0',
        opacity: visible ? 1 : 0,
        transition: 'width 220ms cubic-bezier(0.4, 0, 0.2, 1), opacity 180ms ease-out',
        pointerEvents: visible ? 'auto' : 'none',
      }}
    >
      <div
        className="flex h-[35px] items-center border-b px-4 text-ui font-semibold uppercase tracking-wide"
        style={{ borderColor: '#e0e0e0', color: '#333333' }}
      >
        <span>控制台</span>
      </div>
      <div className="flex-1 overflow-auto">
        <RightTraceView />
      </div>
    </aside>
  );
}
