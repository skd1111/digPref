/**
 * DataWorkbench —— 数据专家主布局（Phase 7 MVP + 2026-08-17 横向布局重构）。
 *
 * 布局（上下两行，用户反馈「还是竖的」后二次重构 2026-08-17）：
 *   ┌─ 左 260px ─┬─ 上行：编辑区（整宽 40%）──────────────────┐
 *   │ 数据源     │ ⌘SQL / 💬对话 页签（Python 已移除）           │
 *   │ 表结构/字典 ├─ 下行：结果区（整宽横向 60%）─────────────┤
 *   │ 历史分析   │ 📋数据网格 / 📈可视化图表 可切换页签（整宽）  │
 *   └────────────┴─────────── 导出 (ExportBar) 通栏置底 ─────┘
 *
 * 结果区不再与编辑器左右分栏，而是横向铺满右侧整行；
 * 网格与图表也从左右并排改为页签切换（2026-08-17 用户要求），
 * 单面板独占整行宽度，宽表/宽图表可直接拿到近全屏宽度。
 *
 * 缩放：网格/图表/对话三个面板头部均有 ⛶ 放大按钮，点击全屏放大查看
 * （网格行高/字号同步放大），Esc 或 ✕ 返回。
 *
 * 与 AuditDashboard 一样是独立全屏布局：WorkspaceLayout 在 mode==='analyst'
 * 时渲染本组件，并隐藏 SideBar/Bottom/Right。
 */
import { useCallback, useEffect, useState } from 'react';
import { DataSourceTree } from '@/components/data/DataSourceTree';
import { HistoryAnalysisList } from '@/components/data/HistoryAnalysisList';
import { QueryEditor } from '@/components/data/QueryEditor';
import { DataGrid } from '@/components/data/DataGrid';
import { ChartPanel } from '@/components/data/ChartPanel';
import { ExportBar } from '@/components/data/ExportBar';
import { DataChatPanel } from '@/components/data/DataChatPanel';

/** 当前全屏放大的面板（null = 无放大） */
type ZoomKind = 'grid' | 'chart' | 'chat' | null;

/** 下行结果区页签：网格 / 图表二选一（独占整行宽度） */
type ResultTab = 'grid' | 'chart';

const RESULT_TABS: Array<{ id: ResultTab; label: string }> = [
  { id: 'grid', label: '数据网格' },
  { id: 'chart', label: '可视化图表' },
];

const ZOOM_TITLES: Record<'grid' | 'chart' | 'chat', string> = {
  grid: '📋 数据网格 · 放大视图',
  chart: '📈 可视化图表 · 放大视图',
  chat: '💬 AI 对话 · 放大视图',
};

export function DataWorkbench(): JSX.Element {
  const [zoomed, setZoomed] = useState<ZoomKind>(null);
  const [resultTab, setResultTab] = useState<ResultTab>('grid');
  const closeZoom = useCallback(() => setZoomed(null), []);

  // Esc 关闭放大视图
  useEffect(() => {
    if (!zoomed) return undefined;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setZoomed(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [zoomed]);

  return (
    <div className="data-workbench flex h-full" style={{ backgroundColor: '#ffffff' }}>
      {/* 左栏：数据源 + 表结构/字典 + 历史分析（系统资产，保持不变） */}
      <div className="flex flex-shrink-0 flex-col border-r" style={{ width: 260, borderColor: '#d4d4d4' }}>
        <div className="min-h-0 flex-1 overflow-hidden">
          <DataSourceTree />
        </div>
        {/* 历史分析（缺口 9） */}
        <div className="flex-shrink-0" style={{ height: 200, borderTop: '1px solid #d0d0d0' }}>
          <HistoryAnalysisList />
        </div>
      </div>

      {/* 右侧主体：上下两行 —— 上行编辑区整宽，下行结果区横向铺满 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* 上行：编辑区（SQL/对话页签，Python 已移除 2026-08-20），整宽 */}
        <div className="min-h-0 overflow-hidden" style={{ flex: '4 1 0%' }}>
          <QueryEditor onChatZoom={() => setZoomed('chat')} />
        </div>

        {/* 下行：结果区横向整行，网格/图表页签切换（单面板独占整行宽度） */}
        <div
          className="flex min-h-0 flex-col border-t"
          style={{ flex: '6 1 0%', borderColor: '#d4d4d4' }}
        >
          {/* 页签栏：数据网格 / 可视化图表 */}
          <div
            className="flex flex-shrink-0 items-center gap-1 border-b px-2 py-1.5"
            style={{ borderColor: '#d0d0d0' }}
          >
            {RESULT_TABS.map((t) => {
              const active = t.id === resultTab;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setResultTab(t.id)}
                  className="rounded px-3 py-1 text-ui font-semibold transition-all"
                  style={{
                    color: active ? '#ffffff' : '#6e6e6e',
                    backgroundColor: active ? '#0e639c' : 'transparent',
                  }}
                >
                  <span className="mr-1" aria-hidden>{t.id === 'grid' ? '📋' : '📈'}</span>
                  {t.label}
                </button>
              );
            })}
          </div>
          {/* 当前页签内容（网格/图表各自保留头部：行数统计/图表类型/⛶放大） */}
          <div className="min-h-0 flex-1 overflow-hidden">
            {resultTab === 'grid' ? (
              <DataGrid onZoom={() => setZoomed('grid')} />
            ) : (
              <ChartPanel onZoom={() => setZoomed('chart')} />
            )}
          </div>
          <ExportBar />
        </div>
      </div>

      {/* 全屏放大视图（网格/图表/对话共用，Esc 或 ✕ 关闭） */}
      {zoomed && (
        <div className="fixed inset-0 z-50 flex flex-col" style={{ backgroundColor: '#ffffff' }}>
          <div
            className="flex h-10 flex-shrink-0 items-center justify-between border-b px-4"
            style={{ borderColor: '#d0d0d0', backgroundColor: '#f3f3f3' }}
          >
            <span className="text-ui font-semibold" style={{ color: '#333333' }}>
              {ZOOM_TITLES[zoomed]}
            </span>
            <button
              type="button"
              onClick={closeZoom}
              className="rounded px-3 py-1 text-ui transition-all hover:brightness-95"
              style={{ backgroundColor: '#ececec', color: '#333333' }}
              title="Esc 关闭"
            >
              ✕ 退出放大
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            {zoomed === 'grid' && <DataGrid zoomed onZoom={closeZoom} />}
            {zoomed === 'chart' && <ChartPanel zoomed onZoom={closeZoom} />}
            {zoomed === 'chat' && <DataChatPanel zoomed onZoom={closeZoom} />}
          </div>
        </div>
      )}
    </div>
  );
}
