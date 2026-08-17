/**
 * 数据专家 HITL 重查询确认流测试（缺口 3）。
 *
 * 覆盖：
 *   - 后端 needs_confirm → store 置 pendingConfirm → QueryEditor 弹确认框
 *   - 点「确认执行」→ confirmed=true 重新提交
 *   - 点「取消」→ 不执行
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryEditor } from '@/components/data/QueryEditor';
import { useDataStore } from '@/store/dataStore';

const dataRunSql = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    dataRunSql: (...args: unknown[]) => dataRunSql(...args),
    dataChartRecommend: vi
      .fn()
      .mockResolvedValue({ chart_type: 'bar', x_index: 0, y_index: 1, reason: '' }),
  },
}));

vi.mock('@/ipc/events', () => ({
  EVT: { DATA_STREAM_CHUNK: 'agent://data_stream_chunk', DATA_STREAM_DONE: 'agent://data_stream_done' },
  listen: vi.fn(),
}));

vi.mock('@monaco-editor/react', () => ({ default: () => null }));

const HEAVY_SQL = 'SELECT a.x, b.y FROM a JOIN b ON a.id=b.id WHERE a.x>0';

function resetStore(): void {
  useDataStore.setState({
    sqlText: HEAVY_SQL,
    editorMode: 'sql',
    pendingConfirm: null,
    result: null,
    running: false,
    error: null,
    // BUGFIX #101 后 RunBar 需选中数据源才显示 HITL 提示，测试需预置
    selectedSourceId: 'src-test',
  });
}

describe('数据专家 HITL 重查询确认（缺口 3）', () => {
  beforeEach(() => {
    resetStore();
    dataRunSql.mockReset();
  });

  it('needs_confirm → 弹确认框；确认后 confirmed=true 重提并渲染结果', async () => {
    dataRunSql.mockResolvedValueOnce({
      needs_confirm: true,
      sql: `${HEAVY_SQL}\nLIMIT 10000`,
      message: '检测到多表 JOIN / 全表扫描，请确认后重新提交（confirmed=true）',
    });
    dataRunSql.mockResolvedValueOnce({
      ok: true,
      task_id: 't1',
      columns: ['x'],
      dtypes: ['int'],
      rows: [[1]],
      row_count: 1,
      elapsed_ms: 5,
      truncated: false,
    });

    render(<QueryEditor />);

    await act(async () => {
      await useDataStore.getState().runQuery();
    });

    // 确认框出现，且尚未第二次提交
    expect(await screen.findByText(/重查询确认/)).toBeTruthy();
    // RunBar 提示与对话框警告各含一处
    expect(screen.getAllByText(/多表 JOIN/).length).toBeGreaterThanOrEqual(2);
    expect(dataRunSql).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('确认执行'));

    await waitFor(() => expect(dataRunSql).toHaveBeenCalledTimes(2));
    // 第二次调用 confirmed=true
    expect(dataRunSql.mock.calls[1][2]).toBe(true);
    await waitFor(() => expect(useDataStore.getState().result?.rowCount).toBe(1));
    expect(useDataStore.getState().pendingConfirm).toBeNull();
  });

  it('取消 → 不执行，pendingConfirm 清空', async () => {
    dataRunSql.mockResolvedValueOnce({
      needs_confirm: true,
      sql: HEAVY_SQL,
      message: '检测到多表 JOIN / 全表扫描',
    });

    render(<QueryEditor />);

    await act(async () => {
      await useDataStore.getState().runQuery();
    });
    expect(await screen.findByText(/重查询确认/)).toBeTruthy();

    fireEvent.click(screen.getByText('取消'));

    await waitFor(() => expect(useDataStore.getState().pendingConfirm).toBeNull());
    expect(dataRunSql).toHaveBeenCalledTimes(1); // 未重提
    expect(useDataStore.getState().result).toBeNull();
  });
});
