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
        created_at: 0,
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

  it('上传：多选文件 → kbUpload 按路径调用', async () => {
    open.mockResolvedValue(['/tmp/a.md', '/tmp/b.pdf']);
    renderPanel();
    await waitFor(() => expect(screen.getByText(/上传参考资料/)).toBeTruthy());
    fireEvent.click(screen.getByText(/上传参考资料/));
    await waitFor(() => expect(kbUpload).toHaveBeenCalledTimes(2));
    expect(kbUpload).toHaveBeenCalledWith('/tmp/a.md', '');
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

  it('更新版本：选文件 → kbUpload 带 baseDocId', async () => {
    open.mockResolvedValue('/tmp/new.md');
    renderPanel();
    await waitFor(() => expect(screen.getByText('更新版本')).toBeTruthy());
    fireEvent.click(screen.getByText('更新版本'));
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
});
