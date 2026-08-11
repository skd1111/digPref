/**
 * 数据专家历史分析列表测试（缺口 9）。
 *
 * 覆盖：
 *   - 挂载时经 ipc.dataListTasks 拉取 /data/tasks
 *   - 列表展示名称/行数
 *   - 点击历史项 → SQL 回填到编辑器（editorMode 切回 sql）
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HistoryAnalysisList } from '@/components/data/HistoryAnalysisList';
import { useDataStore } from '@/store/dataStore';

const dataListTasks = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    dataListTasks: (...args: unknown[]) => dataListTasks(...args),
  },
}));

vi.mock('@/ipc/events', () => ({
  EVT: { DATA_STREAM_CHUNK: 'agent://data_stream_chunk', DATA_STREAM_DONE: 'agent://data_stream_done' },
  listen: vi.fn(),
}));

describe('数据专家历史分析列表（缺口 9）', () => {
  beforeEach(() => {
    useDataStore.setState({ history: [], sqlText: '', editorMode: 'chat' });
    dataListTasks.mockReset();
  });

  it('挂载拉取并展示历史任务', async () => {
    dataListTasks.mockResolvedValue({
      tasks: [
        {
          id: 't1',
          name: 'SELECT * FROM t_order WHERE status=\'1\'',
          query_sql: "SELECT * FROM t_order WHERE status='1'",
          result_metadata: { columns: ['id'], row_count: 128 },
          result_data_ref: '',
          created_at: 1754467200,
        },
      ],
      count: 1,
    });

    render(<HistoryAnalysisList />);

    await waitFor(() => expect(dataListTasks).toHaveBeenCalled());
    expect(await screen.findByText(/t_order/)).toBeTruthy();
    expect(screen.getByText(/128 行/)).toBeTruthy();
  });

  it('点击历史项 → SQL 回填编辑器', async () => {
    dataListTasks.mockResolvedValue({
      tasks: [
        {
          id: 't2',
          name: '坏账率统计',
          query_sql: 'SELECT branch, rate FROM t_bad WHERE m=7',
          result_metadata: { row_count: 10 },
          result_data_ref: '',
          created_at: 1754467200,
        },
      ],
      count: 1,
    });

    render(<HistoryAnalysisList />);

    const item = await screen.findByText('坏账率统计');
    fireEvent.click(item);

    expect(useDataStore.getState().sqlText).toBe('SELECT branch, rate FROM t_bad WHERE m=7');
    expect(useDataStore.getState().editorMode).toBe('sql');
    expect(useDataStore.getState().lastTaskId).toBe('t2');
  });

  it('后端返回异常形态不崩溃', async () => {
    dataListTasks.mockResolvedValue({});
    render(<HistoryAnalysisList />);
    await waitFor(() => expect(dataListTasks).toHaveBeenCalled());
    expect(screen.getByText(/暂无历史分析/)).toBeTruthy();
  });
});
