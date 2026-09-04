/**
 * KnowledgeRagPanel —— 设置页「知识库 / RAG」面板前端回归。
 *
 * 覆盖：
 *   - 挂载后加载并展示概览（文档/分块数、向量模型）
 *   - 修改数值参数 + 保存 → kbConfigSet 收到稀疏 patch + 热生效（无需重启）
 *   - 重建索引按钮 → kbReindex 被调用
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const agentWaitReady = vi.fn();
const kbConfigGet = vi.fn();
const kbStatus = vi.fn();
const kbConfigSet = vi.fn();
const kbReindex = vi.fn();
const agentRestartNow = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    agentWaitReady: (...a: unknown[]) => agentWaitReady(...a),
    kbConfigGet: (...a: unknown[]) => kbConfigGet(...a),
    kbStatus: (...a: unknown[]) => kbStatus(...a),
    kbConfigSet: (...a: unknown[]) => kbConfigSet(...a),
    kbReindex: (...a: unknown[]) => kbReindex(...a),
    agentRestartNow: (...a: unknown[]) => agentRestartNow(...a),
  },
}));

import { KnowledgeRagPanel } from '@/views/settings/KnowledgeRagPanel';

const FULL_CONFIG: Record<string, number | boolean> = {
  rag_enabled: true,
  rag_bm25_enabled: true,
  rag_vector_enabled: true,
  rag_rerank_enabled: true,
  rag_contextual_prefix_enabled: true,
  rag_chunk_size: 512,
  rag_chunk_overlap: 0.1,
  rag_parent_size: 2000,
  rag_top_k: 5,
  rag_candidate_multiplier: 4,
  rag_rrf_k: 60,
  rag_rerank_top_n: 20,
  rag_max_file_mb: 50,
  rag_preload_on_startup: true,
  rag_llm_enhance_enabled: false,
  rag_llm_contextual_enabled: false,
  rag_hyde_enabled: false,
  rag_query_expansion_enabled: false,
};

function seed(): void {
  agentWaitReady.mockClear();
  kbConfigGet.mockClear();
  kbStatus.mockClear();
  kbConfigSet.mockClear();
  kbReindex.mockClear();
  agentRestartNow.mockClear();
  agentWaitReady.mockResolvedValue({ ready: true });
  kbConfigGet.mockResolvedValue({
    config: { ...FULL_CONFIG },
    editable: Object.keys(FULL_CONFIG),
    index_time: [
      'rag_chunk_size',
      'rag_chunk_overlap',
      'rag_parent_size',
      'rag_contextual_prefix_enabled',
      'rag_llm_contextual_enabled',
    ],
    db_path: '/data/knowledge/kb.db',
    kb_dir: '/data/knowledge',
  });
  kbStatus.mockResolvedValue({
    storage_available: true,
    embedding_available: true,
    reranker_available: false,
    stats: { total_docs: 2, total_chunks: 17, by_source_type: {} },
    db_path: '/data/knowledge/kb.db',
    embed_model: 'bge-small-zh-v1.5',
    dim: 512,
    needs_reindex: false,
    reindexing: false,
    reindex_progress: 0,
  });
  kbConfigSet.mockResolvedValue({
    ok: true,
    restart_required: false,
    hot_applied: [],
    needs_reindex: [],
    config: {},
  });
  kbReindex.mockResolvedValue({ ok: true, started: true });
  agentRestartNow.mockResolvedValue({ ok: true, port_freed: true });
}

describe('KnowledgeRagPanel', () => {
  beforeEach(seed);

  it('加载并展示知识库概览', async () => {
    const { container } = render(<KnowledgeRagPanel />);
    await waitFor(() => expect(container.textContent).toContain('知识库概览'));
    expect(agentWaitReady).toHaveBeenCalled();
    expect(container.textContent).toContain('bge-small-zh-v1.5');
    expect(container.textContent).toContain('17');
  });

  it('修改子块大小并保存 → kbConfigSet 收到稀疏 patch', async () => {
    const { container } = render(<KnowledgeRagPanel />);
    await waitFor(() => expect(container.textContent).toContain('知识库概览'));
    const input = container.querySelector('input[min="128"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    fireEvent.change(input, { target: { value: '600' } });
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));
    await waitFor(() => expect(kbConfigSet).toHaveBeenCalledWith({ rag_chunk_size: 600 }));
  });

  it('重建索引按钮触发 kbReindex', async () => {
    render(<KnowledgeRagPanel />);
    await waitFor(() => expect(screen.getByText('重建向量索引')).toBeTruthy());
    fireEvent.click(screen.getByText('重建向量索引'));
    await waitFor(() => expect(kbReindex).toHaveBeenCalled());
  });
});
