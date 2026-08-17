/**
 * DataWorkbench 布局回归（2026-08-17 横向布局二次重构）：
 *   - 对话合并回编辑区页签（SQL/Python/对话三合一，不再是独立横向栏）
 *   - 上下两行：上行编辑区整宽，下行结果区横向整行
 *   - 结果区内数据网格/可视化图表为可切换页签（不再左右并排，单面板独占整行宽度）
 *   - 网格/图表/对话三面板支持 ⛶ 放大（全屏 overlay），Esc 关闭
 *   - 左栏系统资产（数据源树 + 历史分析）保持不变
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/ipc/invoke', () => ({ ipc: {} }));
vi.mock('@/ipc/events', () => ({ EVT: {}, listen: vi.fn() }));
// 布局断言不关心左栏/编辑器内部实现，mock 掉避免拉 monaco/树组件；
// QueryEditor mock 暴露 onChatZoom 触发器，验证对话放大 overlay 仍由 DataWorkbench 承接
vi.mock('@/components/data/DataSourceTree', () => ({
  DataSourceTree: () => <div>数据源树</div>,
}));
vi.mock('@/components/data/HistoryAnalysisList', () => ({
  HistoryAnalysisList: () => <div>历史分析</div>,
}));
vi.mock('@/components/data/QueryEditor', () => ({
  QueryEditor: ({ onChatZoom }: { onChatZoom?: () => void }) => (
    <div>
      编辑器占位
      {onChatZoom && (
        <button type="button" onClick={onChatZoom} title="对话放大">
          对话页签放大
        </button>
      )}
    </div>
  ),
}));
vi.mock('@/components/data/ExportBar', () => ({
  ExportBar: () => <div>导出栏</div>,
}));

import { DataWorkbench } from '@/views/DataWorkbench';
import { useDataStore } from '@/store/dataStore';

function seedResult(): void {
  useDataStore.setState({
    result: {
      columns: ['a', 'b'],
      rows: [[1, 2]],
      rowCount: 1,
      elapsedMs: 3,
      recommendedChart: 'bar',
      chartXIndex: 0,
      chartYIndex: 1,
      taskId: 't1',
    },
  });
}

describe('DataWorkbench 横向布局 + 面板缩放（2026-08-17 重构）', () => {
  beforeEach(() => {
    useDataStore.setState({ result: null, chat: [], chatDraft: '', running: false });
  });

  it('上下两行：左栏资产 + 上行编辑区（含对话页签）+ 下行横向结果区同屏', () => {
    render(<DataWorkbench />);
    // 左栏系统资产保持不变
    expect(screen.getByText('数据源树')).toBeTruthy();
    expect(screen.getByText('历史分析')).toBeTruthy();
    // 上行编辑区（对话已合并为其中页签，不再是独立横向栏）
    expect(screen.getByText('编辑器占位')).toBeTruthy();
    expect(screen.queryByText('💬 AI 对话')).toBeNull();
    // 下行结果区：默认页签为数据网格，图表未渲染（页签切换而非并排）
    expect(screen.getByText('📋 数据网格')).toBeTruthy();
    expect(screen.queryByText('📈 可视化图表')).toBeNull();
    expect(screen.getByText('导出栏')).toBeTruthy();
  });

  it('结果区页签切换：数据网格 ⇄ 可视化图表', () => {
    seedResult();
    render(<DataWorkbench />);
    // 默认网格页签
    expect(screen.getByText('📋 数据网格')).toBeTruthy();
    expect(screen.queryByText('📈 可视化图表')).toBeNull();
    // 切到图表页签
    fireEvent.click(screen.getByText('可视化图表'));
    expect(screen.getByText('📈 可视化图表')).toBeTruthy();
    expect(screen.queryByText('📋 数据网格')).toBeNull();
    // 切回网格页签
    fireEvent.click(screen.getByText('数据网格'));
    expect(screen.getByText('📋 数据网格')).toBeTruthy();
  });

  it('数据网格 ⛶ 放大 → 全屏放大视图，Esc 关闭', () => {
    seedResult();
    render(<DataWorkbench />);
    // 当前激活页签（默认网格）自带放大按钮；对话放大入口在编辑区页签内（mock 另计）
    expect(screen.getAllByTitle('放大查看').length).toBe(1);

    // 精确定位网格面板内的放大按钮（避免 DOM 顺序假设）
    const gridHeader = screen.getByText('📋 数据网格').closest('div')?.parentElement;
    expect(gridHeader).toBeTruthy();
    fireEvent.click(within(gridHeader as HTMLElement).getByTitle('放大查看'));
    expect(screen.getByText('📋 数据网格 · 放大视图')).toBeTruthy();
    // 放大视图内按钮变为「退出放大」
    expect(screen.getAllByTitle('退出放大').length).toBeGreaterThanOrEqual(1);

    // Esc 关闭
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByText('📋 数据网格 · 放大视图')).toBeNull();
  });

  it('对话页签 ⛶ 放大 → 放大视图显示对话内容', () => {
    useDataStore.setState({
      chat: [{ role: 'user', content: '对比上月各分行坏账率' }],
    });
    render(<DataWorkbench />);
    // 对话放大入口在编辑区页签内（经 onChatZoom 回调触发）
    fireEvent.click(screen.getByText('对话页签放大'));
    expect(screen.getByText('💬 AI 对话 · 放大视图')).toBeTruthy();
    expect(screen.getAllByText('对比上月各分行坏账率').length).toBeGreaterThanOrEqual(1);
  });
});
