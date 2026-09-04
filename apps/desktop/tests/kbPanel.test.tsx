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

  it('删除：确认后调用 kbDelete', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('删除')).toBeTruthy());
    fireEvent.click(screen.getByText('删除'));
    await waitFor(() => expect(kbDelete).toHaveBeenCalledWith('d1'));
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
});
