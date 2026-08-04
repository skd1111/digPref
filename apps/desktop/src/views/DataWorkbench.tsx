/**
 * DataWorkbench —— 数据专家主布局（Phase 7 MVP，前端 + mock 数据）。
 *
 * 布局（design/phase-7-data-expert.md §3，四象限）：
 *   ┌─ 左 240px ─┬─ 中 (flex-1) ────────┬─ 右 380px ──────────┐
 *   │ 数据源     │  模式切换 [SQL|Py|对话]│ 数据网格 (DataGrid) │
 *   │ 表结构/字典 │  编辑器 + AI 助手     │ 可视化 (ChartPanel) │
 *   │ 历史分析   │                       │ 导出 (ExportBar)    │
 *   └────────────┴───────────────────────┴─────────────────────┘
 *
 * 与 AuditDashboard 一样是独立全屏布局：WorkspaceLayout 在 mode==='analyst'
 * 时渲染本组件，并隐藏 SideBar/Bottom/Right。
 */
import { DataSourceTree } from '@/components/data/DataSourceTree';
import { QueryEditor } from '@/components/data/QueryEditor';
import { DataGrid } from '@/components/data/DataGrid';
import { ChartPanel } from '@/components/data/ChartPanel';
import { ExportBar } from '@/components/data/ExportBar';

export function DataWorkbench(): JSX.Element {
  return (
    <div className="data-workbench flex h-full" style={{ backgroundColor: '#ffffff' }}>
      {/* 左栏：数据源 + 表结构/字典 + 历史分析 */}
      <div className="flex-shrink-0 border-r" style={{ width: 260, borderColor: '#d4d4d4' }}>
        <DataSourceTree />
      </div>

      {/* 中栏：编辑器（SQL/Python/对话） */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <QueryEditor />
      </div>

      {/* 右栏：数据网格 + 图表 + 导出 */}
      <div
        className="flex flex-shrink-0 flex-col overflow-hidden border-l"
        style={{ width: 380, borderColor: '#d4d4d4' }}
      >
        <div className="flex-1 overflow-hidden" style={{ minHeight: 160 }}>
          <DataGrid />
        </div>
        <div className="flex-shrink-0" style={{ height: 240, borderTop: '1px solid #d0d0d0' }}>
          <ChartPanel />
        </div>
        <ExportBar />
      </div>
    </div>
  );
}
