/**
 * DataSourceTree 左栏交互测试（2026-08-20 用户需求）。
 *
 * 覆盖：
 *   - 双击表名 → 预览数据（直接执行预览 SQL，不覆盖编辑器已有内容）
 *   - 数据源可收起（折叠箭头切换，不影响查询用源选择）
 *   - ＋ 新增数据源入口常驻（支持配置多个数据源）
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DataSourceTree } from '@/components/data/DataSourceTree';
import { useAssetStore } from '@/store/assetStore';
import { useDataStore } from '@/store/dataStore';

const listAssets = vi.fn();
const dataListSources = vi.fn();
const dataSyncSchema = vi.fn();
const dataRunSql = vi.fn();
const dataChartRecommend = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    listAssets: (...args: unknown[]) => listAssets(...args),
    dataListSources: (...args: unknown[]) => dataListSources(...args),
    dataSyncSchema: (...args: unknown[]) => dataSyncSchema(...args),
    dataRunSql: (...args: unknown[]) => dataRunSql(...args),
    dataChartRecommend: (...args: unknown[]) => dataChartRecommend(...args),
  },
}));

vi.mock('@/ipc/events', () => ({
  EVT: { DATA_STREAM_CHUNK: 'agent://data_stream_chunk', DATA_STREAM_DONE: 'agent://data_stream_done' },
  listen: vi.fn(),
}));

vi.mock('@/components/asset-tree/AssetConfigDialog', () => ({
  AssetConfigDialog: () => <div>资产配置弹窗</div>,
}));

function seedStores(): void {
  useAssetStore.setState({
    tree: [{ id: 'src-a', type: 'database', label: 'MySQL A', icon: '', meta: { db_type: 'mysql' } }],
    loading: false,
    error: null,
  });
  useDataStore.setState({
    sources: [
      {
        id: 'src-a',
        name: 'MySQL A',
        type: 'mysql',
        status: 'connected',
        tables: [{ name: 't1', comment: '场景表', columns: [{ name: 'c1', type: 'int', comment: '' }] }],
      },
    ],
    selectedSourceId: 'src-a',
    selectedTable: null,
    loading: false,
    error: null,
    sqlText: 'SELECT 42; -- 用户已写的内容',
    sqlSelection: '',
    editorMode: 'chat',
    syncing: false,
  });
  listAssets.mockResolvedValue([
    { id: 'src-a', type: 'database', label: 'MySQL A', meta: { db_type: 'mysql' } },
  ]);
  dataListSources.mockResolvedValue({
    sources: [
      {
        id: 'src-a',
        name: 'MySQL A',
        type: 'mysql',
        schema_cache: [{ name: 't1', comment: '场景表', columns: [{ name: 'c1', dtype: 'int' }] }],
      },
    ],
    count: 1,
  });
  dataRunSql.mockResolvedValue({
    ok: true,
    columns: ['c1'],
    rows: [],
    row_count: 0,
    elapsed_ms: 1,
    truncated: false,
    task_id: 'task-preview',
  });
  dataChartRecommend.mockResolvedValue({ chart_type: 'bar', x_index: 0, y_index: 0 });
}

describe('DataSourceTree：多数据源 / 收起 / 双击预览', () => {
  beforeEach(() => {
    seedStores();
    dataSyncSchema.mockReset().mockResolvedValue({ ok: true });
    dataRunSql.mockClear();
  });

  it('双击表名 → 直接执行预览 SQL，且不覆盖编辑器已有内容', async () => {
    render(<DataSourceTree />);
    const tbl = await screen.findByText('t1');
    fireEvent.doubleClick(tbl);

    await waitFor(() => expect(dataRunSql).toHaveBeenCalled());
    expect(dataRunSql).toHaveBeenCalledWith('SELECT * FROM t1 LIMIT 200;', 'src-a', false);
    // 编辑器内容原样保留，只切到 SQL 模式看结果
    expect(useDataStore.getState().sqlText).toBe('SELECT 42; -- 用户已写的内容');
    expect(useDataStore.getState().editorMode).toBe('sql');
    expect(useDataStore.getState().selectedSourceId).toBe('src-a');
  });

  it('点折叠箭头收起数据源 → 表列表隐藏，再点展开', async () => {
    render(<DataSourceTree />);
    await screen.findByText('t1');

    fireEvent.click(screen.getByLabelText('收起'));
    expect(screen.queryByText('t1')).toBeNull();

    fireEvent.click(screen.getByLabelText('展开'));
    expect(screen.getByText('t1')).toBeTruthy();
  });

  it('已有数据源时 ＋ 新增入口仍常驻（支持配置多个）', async () => {
    render(<DataSourceTree />);
    await screen.findByText('t1');
    expect(screen.getByTitle('新增数据源（支持配置多个）')).toBeTruthy();
  });
});
