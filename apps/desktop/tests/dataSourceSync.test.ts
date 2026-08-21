/**
 * 数据源表结构同步 + SQL 执行链路测试（缺口 2 + BUGFIX #125/#126/#129/#130）。
 *
 * 覆盖：
 *   - syncSchemas：每源只自动同步一次（防 fetch→sync→重拉死循环）
 *   - refreshSchemas：手动刷新清掉限制，同一源可再次同步（刷新按钮链路）
 *   - runQuery 选区优先：有选中只执行选区，无选区执行全部
 *   - runPreviewSql：直接执行指定 SQL，不动编辑器内容
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useDataStore } from '@/store/dataStore';

const dataSyncSchema = vi.fn();
const dataListSources = vi.fn();
const dataRunSql = vi.fn();
const dataChartRecommend = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    dataSyncSchema: (...args: unknown[]) => dataSyncSchema(...args),
    dataListSources: (...args: unknown[]) => dataListSources(...args),
    dataRunSql: (...args: unknown[]) => dataRunSql(...args),
    dataChartRecommend: (...args: unknown[]) => dataChartRecommend(...args),
  },
}));

vi.mock('@/ipc/events', () => ({
  EVT: { DATA_STREAM_CHUNK: 'agent://data_stream_chunk', DATA_STREAM_DONE: 'agent://data_stream_done' },
  listen: vi.fn(),
}));

describe('数据源表结构同步（syncSchemas / refreshSchemas）', () => {
  beforeEach(() => {
    useDataStore.setState({ sources: [], syncing: false });
    dataSyncSchema.mockReset().mockResolvedValue({ ok: true, tables_synced: 0 });
    dataListSources.mockReset().mockResolvedValue({ sources: [], count: 0 });
  });
  it('自动同步每源只试一次，重复调用不再发请求', async () => {
    await useDataStore.getState().syncSchemas(['src-auto-1']);
    await useDataStore.getState().syncSchemas(['src-auto-1']);
    expect(dataSyncSchema).toHaveBeenCalledTimes(1);
    expect(dataSyncSchema).toHaveBeenCalledWith('src-auto-1');
  });

  it('手动刷新（refreshSchemas）可强制重同步同一源，并置 syncing 状态', async () => {
    await useDataStore.getState().syncSchemas(['src-btn-1']);
    expect(dataSyncSchema).toHaveBeenCalledTimes(1);

    const p = useDataStore.getState().refreshSchemas(['src-btn-1']);
    expect(useDataStore.getState().syncing).toBe(true);
    await p;
    expect(useDataStore.getState().syncing).toBe(false);
    expect(dataSyncSchema).toHaveBeenCalledTimes(2);
    // 同步完成后重拉数据源列表（树刷新）
    expect(dataListSources).toHaveBeenCalled();
  });

  it('空 ids 不发请求不报错', async () => {
    await useDataStore.getState().refreshSchemas([]);
    await useDataStore.getState().syncSchemas(['', '   ']);
    expect(dataSyncSchema).not.toHaveBeenCalled();
  });
});

describe('SQL 执行选区优先 + 预览不覆盖编辑器（2026-08-20）', () => {
  beforeEach(() => {
    useDataStore.setState({
      sqlText: 'SELECT * FROM a;\nSELECT * FROM b;',
      sqlSelection: '',
      selectedSourceId: 'src-x',
      running: false,
      error: null,
      pendingConfirm: null,
    });
    dataRunSql.mockReset().mockResolvedValue({
      ok: true,
      columns: ['c'],
      rows: [],
      row_count: 0,
      elapsed_ms: 1,
      truncated: false,
      task_id: 't',
    });
    dataChartRecommend.mockReset().mockResolvedValue({ chart_type: 'bar', x_index: 0, y_index: 0 });
  });

  it('有选区 → 只执行选中部分', async () => {
    useDataStore.setState({ sqlSelection: 'SELECT * FROM b;' });
    await useDataStore.getState().runQuery();
    expect(dataRunSql).toHaveBeenCalledWith('SELECT * FROM b;', 'src-x', false);
  });

  it('无选区（含纯空白）→ 执行全部', async () => {
    useDataStore.setState({ sqlSelection: '   ' });
    await useDataStore.getState().runQuery();
    expect(dataRunSql).toHaveBeenCalledWith('SELECT * FROM a;\nSELECT * FROM b;', 'src-x', false);
  });

  it('runPreviewSql 直接执行指定 SQL，不碰编辑器内容与选区', async () => {
    useDataStore.setState({ sqlSelection: 'SELECT * FROM b;' });
    await useDataStore.getState().runPreviewSql('SELECT * FROM t9 LIMIT 200;');
    expect(dataRunSql).toHaveBeenCalledWith('SELECT * FROM t9 LIMIT 200;', 'src-x', false);
    expect(useDataStore.getState().sqlText).toBe('SELECT * FROM a;\nSELECT * FROM b;');
  });
});
