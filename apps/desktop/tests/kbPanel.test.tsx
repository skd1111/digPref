/**
 * KbPanel —— 审核专家「知识库 / 参考资料」顶层页签前端回归。
 *
 * 覆盖：
 *   - 挂载后加载并展示已入库文档（文件名 + 状态 + 分块数）
 *   - 上传：文件对话框多选 → kbUpload 按路径调用
 *   - 删除：确认后 kbDelete 被调用
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const agentWaitReady = vi.fn();
const kbList = vi.fn();
const kbUpload = vi.fn();
const kbDelete = vi.fn();
const kbStatus = vi.fn();
const kbReindex = vi.fn();
const kbVersions = vi.fn();
const kbActivate = vi.fn();
const kbSetCategory = vi.fn();
const kbClassifyPreview = vi.fn();
const open = vi.fn();
const previewLocalFile = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    agentWaitReady: (...a: unknown[]) => agentWaitReady(...a),
    kbList: (...a: unknown[]) => kbList(...a),
    kbUpload: (...a: unknown[]) => kbUpload(...a),
    kbDelete: (...a: unknown[]) => kbDelete(...a),
    kbStatus: (...a: unknown[]) => kbStatus(...a),
    kbReindex: (...a: unknown[]) => kbReindex(...a),
    kbVersions: (...a: unknown[]) => kbVersions(...a),
    kbActivate: (...a: unknown[]) => kbActivate(...a),
    kbSetCategory: (...a: unknown[]) => kbSetCategory(...a),
    kbClassifyPreview: (...a: unknown[]) => kbClassifyPreview(...a),
  },
}));

vi.mock('@/store/officePreviewStore', () => ({
  previewLocalFile: (...a: unknown[]) => previewLocalFile(...a),
}));

vi.mock('@tauri-apps/plugin-dialog', () => ({
  open: (...a: unknown[]) => open(...a),
}));

import { KbPanel } from '@/components/doc-review/KbPanel';

function renderPanel(): ReturnType<typeof render> {
  return render(
    <MemoryRouter>
      <KbPanel />
    </MemoryRouter>,
  );
}

function seed(): void {
  agentWaitReady.mockClear();
  kbList.mockClear();
  kbUpload.mockClear();
  kbDelete.mockClear();
  kbStatus.mockClear();
  kbReindex.mockClear();
  kbVersions.mockClear();
  kbActivate.mockClear();
  kbSetCategory.mockClear();
  kbClassifyPreview.mockClear();
  open.mockClear();
  previewLocalFile.mockClear();
  previewLocalFile.mockResolvedValue(undefined);
  agentWaitReady.mockResolvedValue({ ready: true });
  kbList.mockResolvedValue({
    total: 1,
    docs: [
      {
        id: 'd1',
        title: '报销制度',
        file_name: 'baoxiao.md',
        source_type: 'md',
        category: 'finance',
        status: 'ready',
        error: null,
        chunk_count: 8,
        size_bytes: 1024,
        created_at: 1767225600,
        updated_at: 0,
        group_id: 'd1',
        version: 1,
        is_active: true,
        version_count: 1,
        file_path: '/data/knowledge/files/d1_baoxiao.md',
      },
    ],
  });
  kbStatus.mockResolvedValue({
    storage_available: true,
    embedding_available: true,
    reranker_available: true,
    stats: { total_docs: 1, total_chunks: 8, by_source_type: {} },
    db_path: '/data/knowledge/kb.db',
    embed_model: 'bge-small-zh-v1.5',
    dim: 512,
    needs_reindex: false,
    reindexing: false,
    reindex_progress: 0,
  });
  kbUpload.mockResolvedValue({ doc_id: 'd2', status: 'indexing' });
  kbDelete.mockResolvedValue({ deleted: true });
  kbReindex.mockResolvedValue({ ok: true, started: true });
  kbActivate.mockResolvedValue({ ok: true, group_id: 'g1', active_doc_id: 'dV1' });
  kbVersions.mockResolvedValue({ group_id: 'd1', active_doc_id: 'd1', versions: [] });
  kbSetCategory.mockResolvedValue({ ok: true, group_id: 'd1', category: 'legal' });
  // 默认：自动分类未启用（预判端点返回 enabled=false，不回填分类）
  kbClassifyPreview.mockResolvedValue({ enabled: false, reason: '自动分类未启用', items: [] });
}

/** 多版本资料：主列表一条生效版 v2（共 2 版），版本历史返回 v2(生效)+v1(归档)。 */
function seedMultiVersion(): void {
  kbList.mockResolvedValue({
    total: 1,
    docs: [
      {
        id: 'dV2',
        title: '报销制度',
        file_name: 'baoxiao_v2.md',
        source_type: 'md',
        category: 'finance',
        status: 'ready',
        error: null,
        chunk_count: 8,
        size_bytes: 1024,
        created_at: 0,
        updated_at: 0,
        group_id: 'g1',
        version: 2,
        is_active: true,
        version_count: 2,
        file_path: '/x/dV2.md',
      },
    ],
  });
  kbVersions.mockResolvedValue({
    group_id: 'g1',
    active_doc_id: 'dV2',
    versions: [
      {
        doc_id: 'dV2', group_id: 'g1', version: 2, status: 'ready', is_active: true,
        chunk_count: 8, size_bytes: 1024, file_name: 'baoxiao_v2.md', created_at: 0,
        updated_at: 0, error: null, file_path: '/x/dV2.md',
      },
      {
        doc_id: 'dV1', group_id: 'g1', version: 1, status: 'ready', is_active: false,
        chunk_count: 6, size_bytes: 900, file_name: 'baoxiao_v1.md', created_at: 0,
        updated_at: 0, error: null, file_path: '/x/dV1.md',
      },
    ],
  });
}

/** 两条文档：用于批量勾选 / 批量删除用例。 */
function seedTwoDocs(): void {
  kbList.mockResolvedValue({
    total: 2,
    docs: [
      {
        id: 'd1', title: '报销制度', file_name: 'baoxiao.md', source_type: 'md',
        category: 'finance', status: 'ready', error: null, chunk_count: 8,
        size_bytes: 1024, created_at: 0, updated_at: 0, group_id: 'd1', version: 1,
        is_active: true, version_count: 1, file_path: '/data/knowledge/files/d1_baoxiao.md',
      },
      {
        id: 'd2', title: '差旅制度', file_name: 'chailv.md', source_type: 'md',
        category: 'finance', status: 'ready', error: null, chunk_count: 5,
        size_bytes: 2048, created_at: 0, updated_at: 0, group_id: 'd2', version: 1,
        is_active: true, version_count: 1, file_path: '/data/knowledge/files/d2_chailv.md',
      },
    ],
  });
}

describe('KbPanel', () => {
  beforeEach(() => {
    seed();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('加载并展示已入库文档', async () => {
    const { container } = renderPanel();
    await waitFor(() => expect(container.textContent).toContain('baoxiao.md'));
    expect(agentWaitReady).toHaveBeenCalled();
    expect(container.textContent).toContain('就绪');
    expect(container.textContent).toContain('8 块');
  });

  it('上传：先出待导入清单，确认后才入库（未确认前不调用 kbUpload）', async () => {
    open.mockResolvedValue(['/tmp/a.md', '/tmp/b.pdf']);
    renderPanel();
    await waitFor(() => expect(screen.getByText(/上传参考资料/)).toBeTruthy());
    fireEvent.click(screen.getByText(/上传参考资料/));
    await waitFor(() => expect(screen.getByLabelText('确认导入参考资料')).toBeTruthy());
    expect(kbUpload).not.toHaveBeenCalled();
    expect(screen.getByText('a.md')).toBeTruthy();
    expect(screen.getByText('b.pdf')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /确认导入/ }));
    await waitFor(() => expect(kbUpload).toHaveBeenCalledTimes(2));
    expect(kbUpload).toHaveBeenCalledWith('/tmp/a.md', '', undefined);
    expect(kbUpload).toHaveBeenCalledWith('/tmp/b.pdf', '', undefined);
  });

  it('导入确认：可改分类、可删除某一条，确认后只导入剩余条目', async () => {
    open.mockResolvedValue(['/tmp/a.md', '/tmp/b.pdf']);
    renderPanel();
    fireEvent.click(await screen.findByText(/上传参考资料/));
    await waitFor(() => expect(screen.getByLabelText('确认导入参考资料')).toBeTruthy());
    // 改第一条的分类
    const catSelects = screen.getAllByTitle('导入时打的分类标签（可在入库前手动调整）');
    fireEvent.change(catSelects[0], { target: { value: 'legal' } });
    // 删除第二条
    fireEvent.click(screen.getAllByTitle('从待导入清单移除该文件')[1]);
    await waitFor(() => expect(screen.queryByText('b.pdf')).toBeNull());
    // 确认后才入库
    fireEvent.click(screen.getByRole('button', { name: /确认导入/ }));
    await waitFor(() => expect(kbUpload).toHaveBeenCalledTimes(1));
    expect(kbUpload).toHaveBeenCalledWith('/tmp/a.md', 'legal', undefined);
  });

  it('导入前自动分类预判：结果回填清单，导入时直接带上该分类（不再二次判定）', async () => {
    kbClassifyPreview.mockResolvedValue({
      enabled: true,
      items: [
        { path: '/tmp/a.md', category: 'finance', label: '财务/财税', error: '' },
        { path: '/tmp/b.pdf', category: 'legal', label: '法务/合同', error: '' },
      ],
    });
    open.mockResolvedValue(['/tmp/a.md', '/tmp/b.pdf']);
    renderPanel();
    fireEvent.click(await screen.findByText(/上传参考资料/));
    await waitFor(() =>
      expect(kbClassifyPreview).toHaveBeenCalledWith(['/tmp/a.md', '/tmp/b.pdf']),
    );
    // 分类已回填并标记为 AI 判定
    await waitFor(() => expect(screen.getAllByText('AI 判定').length).toBe(2));
    fireEvent.click(screen.getByRole('button', { name: /确认导入/ }));
    await waitFor(() => expect(kbUpload).toHaveBeenCalledTimes(2));
    expect(kbUpload).toHaveBeenCalledWith('/tmp/a.md', 'finance', undefined);
    expect(kbUpload).toHaveBeenCalledWith('/tmp/b.pdf', 'legal', undefined);
  });

  it('导入前自动分类预判：用户手改分类后以用户为准', async () => {
    kbClassifyPreview.mockResolvedValue({
      enabled: true,
      items: [{ path: '/tmp/a.md', category: 'finance', label: '财务/财税', error: '' }],
    });
    open.mockResolvedValue(['/tmp/a.md']);
    renderPanel();
    fireEvent.click(await screen.findByText(/上传参考资料/));
    await waitFor(() => expect(screen.getAllByText('AI 判定').length).toBe(1));
    const catSelects = screen.getAllByTitle('导入时打的分类标签（可在入库前手动调整）');
    fireEvent.change(catSelects[0], { target: { value: 'compliance' } });
    fireEvent.click(screen.getByRole('button', { name: /确认导入/ }));
    await waitFor(() => expect(kbUpload).toHaveBeenCalledTimes(1));
    expect(kbUpload).toHaveBeenCalledWith('/tmp/a.md', 'compliance', undefined);
  });

  it('导入前自动分类预判：失败时行内显示原因并提供「重试分类」', async () => {
    kbClassifyPreview.mockResolvedValue({
      enabled: true,
      items: [{ path: '/tmp/a.md', category: '', label: '', error: '分类失败：429 限流' }],
    });
    open.mockResolvedValue(['/tmp/a.md']);
    renderPanel();
    fireEvent.click(await screen.findByText(/上传参考资料/));
    await waitFor(() => expect(screen.getByText(/分类失败/)).toBeTruthy());
    expect(screen.getByRole('button', { name: '重试分类' })).toBeTruthy();
  });

  it('导入前自动分类预判：启发式兜底结果标「建议分类」', async () => {
    kbClassifyPreview.mockResolvedValue({
      enabled: true,
      items: [
        { path: '/tmp/a.md', category: 'legal', label: '法务/合同', error: '', source: 'heuristic' },
      ],
    });
    open.mockResolvedValue(['/tmp/a.md']);
    renderPanel();
    fireEvent.click(await screen.findByText(/上传参考资料/));
    await waitFor(() => expect(screen.getByText('建议分类')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /确认导入/ }));
    await waitFor(() => expect(kbUpload).toHaveBeenCalledWith('/tmp/a.md', 'legal', undefined));
  });

  it('预判遇 429 限流：给出可读中文原因（不再抛原始错误）', async () => {
    kbClassifyPreview.mockRejectedValue(new Error('LLM HTTP 429 rate_limit_error 速率限制'));
    open.mockResolvedValue(['/tmp/a.md']);
    renderPanel();
    fireEvent.click(await screen.findByText(/上传参考资料/));
    await waitFor(() => expect(screen.getByText(/模型限流/)).toBeTruthy(), { timeout: 8000 });
  });

  it('预判分批：条数多时按批调用（每批 5 个）', async () => {
    const paths = Array.from({ length: 12 }, (_, i) => `/tmp/f${i}.md`);
    kbClassifyPreview.mockImplementation(async (p: string[]) => ({
      enabled: true,
      items: p.map((path) => ({
        path,
        category: 'finance',
        label: '财务/财税',
        error: '',
        source: 'llm',
      })),
    }));
    open.mockResolvedValue(paths);
    renderPanel();
    fireEvent.click(await screen.findByText(/上传参考资料/));
    await waitFor(() => expect(kbClassifyPreview).toHaveBeenCalledTimes(3), { timeout: 8000 });
    const sizes = kbClassifyPreview.mock.calls.map((c) => (c[0] as string[]).length);
    expect(sizes).toEqual([5, 5, 2]);
  });

  it('导入确认：可取消，取消后不入库', async () => {
    open.mockResolvedValue(['/tmp/a.md']);
    renderPanel();
    fireEvent.click(await screen.findByText(/上传参考资料/));
    await waitFor(() => expect(screen.getByLabelText('确认导入参考资料')).toBeTruthy());
    fireEvent.click(screen.getByText('取消'));
    await waitFor(() => expect(screen.queryByLabelText('确认导入参考资料')).toBeNull());
    expect(kbUpload).not.toHaveBeenCalled();
  });

  it('列表展示导入时间列', async () => {
    const { container } = renderPanel();
    await waitFor(() => expect(container.textContent).toContain('baoxiao.md'));
    expect(screen.getByText('导入时间')).toBeTruthy();
    expect(container.textContent).toMatch(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
  });

  it('删除：确认后调用 kbDelete（整份资料 all_versions=true）', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('删除')).toBeTruthy());
    fireEvent.click(screen.getByText('删除'));
    await waitFor(() => expect(kbDelete).toHaveBeenCalledWith('d1', true));
  });

  it('点击文件名预览已入库源文件', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('baoxiao.md')).toBeTruthy());
    fireEvent.click(screen.getByText('baoxiao.md'));
    await waitFor(() =>
      expect(previewLocalFile).toHaveBeenCalledWith('/data/knowledge/files/d1_baoxiao.md'),
    );
  });

  it('点击「预览」按钮同样触发预览', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('预览')).toBeTruthy());
    fireEvent.click(screen.getByText('预览'));
    await waitFor(() =>
      expect(previewLocalFile).toHaveBeenCalledWith('/data/knowledge/files/d1_baoxiao.md'),
    );
  });

  it('多版本资料显示版本徽标（v/共N版）', async () => {
    seedMultiVersion();
    const { container } = renderPanel();
    await waitFor(() => expect(container.textContent).toContain('共2版'));
    expect(container.textContent).toContain('v2');
  });

  it('更新版本：选文件 → 确认后 kbUpload 带 baseDocId', async () => {
    open.mockResolvedValue('/tmp/new.md');
    renderPanel();
    await waitFor(() => expect(screen.getByText('更新版本')).toBeTruthy());
    fireEvent.click(screen.getByText('更新版本'));
    await waitFor(() => expect(screen.getByLabelText('确认导入新版本')).toBeTruthy());
    expect(kbUpload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /确认导入/ }));
    await waitFor(() => expect(kbUpload).toHaveBeenCalledWith('/tmp/new.md', 'finance', 'd1'));
  });

  it('版本历史：展开列出全部版本，旧版本可设为生效', async () => {
    seedMultiVersion();
    renderPanel();
    await waitFor(() => expect(screen.getByText(/版本历史/)).toBeTruthy());
    fireEvent.click(screen.getByText(/版本历史/));
    await waitFor(() => expect(screen.getByText('baoxiao_v1.md')).toBeTruthy());
    expect(kbVersions).toHaveBeenCalledWith('dV2');
    expect(screen.getByText('● 生效')).toBeTruthy();
    expect(screen.getByText('○ 已归档')).toBeTruthy();
    // 生效版本 v2 的「设为生效」禁用，旧版本 v1 可点（倒序第 2 个）
    const activateBtns = screen.getAllByText('设为生效');
    fireEvent.click(activateBtns[1]);
    await waitFor(() => expect(kbActivate).toHaveBeenCalledWith('dV1'));
  });

  it('版本历史：删除单个版本调用 kbDelete(id, false)', async () => {
    seedMultiVersion();
    renderPanel();
    fireEvent.click(await screen.findByText(/版本历史/));
    await waitFor(() => expect(screen.getByText('baoxiao_v1.md')).toBeTruthy());
    // 主行删除 + v2/v1 两行删除，最后一个为 v1 行
    const delBtns = screen.getAllByText('删除');
    fireEvent.click(delBtns[delBtns.length - 1]);
    await waitFor(() => expect(kbDelete).toHaveBeenCalledWith('dV1', false));
  });

  it('修改分类：下拉变更 → kbSetCategory', async () => {
    renderPanel();
    const select = await screen.findByTitle(
      '修改该资料的分类（作用于全部版本，并同步用于按类检索）',
    );
    fireEvent.change(select, { target: { value: 'legal' } });
    await waitFor(() => expect(kbSetCategory).toHaveBeenCalledWith('d1', 'legal'));
  });

  it('每条数据都有复选框，勾选后可批量删除（含全部版本）', async () => {
    seedTwoDocs();
    renderPanel();
    await waitFor(() => expect(screen.getByLabelText('选择 baoxiao.md')).toBeTruthy());
    fireEvent.click(screen.getByLabelText('选择 baoxiao.md'));
    fireEvent.click(screen.getByLabelText('选择 chailv.md'));
    await waitFor(() => expect(screen.getByText('批量删除 (2)')).toBeTruthy());
    fireEvent.click(screen.getByText('批量删除 (2)'));
    await waitFor(() => expect(kbDelete).toHaveBeenCalledTimes(2));
    expect(kbDelete).toHaveBeenCalledWith('d1', true);
    expect(kbDelete).toHaveBeenCalledWith('d2', true);
  });

  it('表头全选框可一次选中全部，批量删除按钮未选中时禁用', async () => {
    seedTwoDocs();
    renderPanel();
    await waitFor(() => expect(screen.getByLabelText('全选')).toBeTruthy());
    expect((screen.getByText('批量删除') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText('全选'));
    await waitFor(() => expect(screen.getByText('批量删除 (2)')).toBeTruthy());
    fireEvent.click(screen.getByText('批量删除 (2)'));
    await waitFor(() => expect(kbDelete).toHaveBeenCalledTimes(2));
  });
});
