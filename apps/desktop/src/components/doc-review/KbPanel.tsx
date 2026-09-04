/**
 * KbPanel —— 审核专家「知识库 / 参考资料」页（与「审批工作台」「文档审核」平级的顶层页签）。
 *
 * 上传的参考资料经 RAG 分块 + 向量化入库，文档审核与聊天时按混合检索
 * （SQLite FTS5 BM25 + sqlite-vec 向量 + RRF + ONNX reranker）召回注入提示词。
 * 上传走本地路径（Tauri 对话框/多选），文件复制入库，数据落安装目录 knowledge/，复制即迁移。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { useNavigate } from 'react-router-dom';
import { ipc } from '@/ipc/invoke';
import type { KbDocSummary, KbStatus } from '@/ipc/invoke';
import { previewLocalFile } from '@/store/officePreviewStore';

const SUPPORTED_EXTENSIONS = [
  'pdf', 'docx', 'doc', 'txt', 'md', 'csv', 'html', 'htm', 'xlsx', 'pptx',
];

const CATEGORIES: { value: string; label: string }[] = [
  { value: '', label: '未分类' },
  { value: 'finance', label: '财务/财税' },
  { value: 'legal', label: '法务/合同' },
  { value: 'compliance', label: '合规/制度' },
  { value: 'data_security', label: '数据安全' },
];

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: '待索引', color: '#616161' },
  indexing: { label: '索引中', color: '#0e639c' },
  ready: { label: '就绪', color: '#059669' },
  failed: { label: '失败', color: '#cd3131' },
  stale: { label: '需重建', color: '#b25c1a' },
};

function extOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : '';
}

function fmtSize(n: number): string {
  if (!n) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function KbPanel(): JSX.Element {
  const navigate = useNavigate();
  const [docs, setDocs] = useState<KbDocSummary[]>([]);
  const [status, setStatus] = useState<KbStatus | null>(null);
  const [category, setCategory] = useState('');
  const [busy, setBusy] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const [list, st] = await Promise.all([ipc.kbList(), ipc.kbStatus()]);
      setDocs(list.docs ?? []);
      setStatus(st);
      setReindexing(st.reindexing);
    } catch (e) {
      setError(`读取知识库失败 · ${String(e)}`);
    }
  }, []);

  // 有文档在索引中或正在重建 → 轮询，直到落定
  useEffect(() => {
    const busyNow =
      reindexing || docs.some((d) => d.status === 'indexing' || d.status === 'pending');
    if (busyNow && pollRef.current === null) {
      pollRef.current = window.setInterval(() => void refresh(), 1500);
    } else if (!busyNow && pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current !== null && !busyNow) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [docs, reindexing, refresh]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const ready = await ipc.agentWaitReady(15);
        if (!ready.ready) {
          if (!cancelled) setError(`Agent 未就绪（${ready.error ?? 'timeout'}）`);
          return;
        }
        if (!cancelled) await refresh();
      } catch (e) {
        if (!cancelled) setError(`初始化失败 · ${String(e)}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const onUpload = async (): Promise<void> => {
    setError(null);
    let picked: string | string[] | null = null;
    try {
      picked = await open({
        multiple: true,
        filters: [{ name: '参考资料', extensions: SUPPORTED_EXTENSIONS }],
      });
    } catch (e) {
      setError(`打开文件对话框失败 · ${String(e)}`);
      return;
    }
    if (!picked) return;
    const paths = Array.isArray(picked) ? picked : [picked];
    const supported = paths.filter((p) => SUPPORTED_EXTENSIONS.includes(extOf(p)));
    if (supported.length === 0) {
      setError('没有受支持的文件（pdf/docx/doc/txt/md/csv/html/xlsx/pptx）');
      return;
    }
    setBusy(true);
    const failed: string[] = [];
    for (const p of supported) {
      try {
        await ipc.kbUpload(p, category);
      } catch (e) {
        failed.push(`${p.split(/[\\/]/).pop()}: ${String(e)}`);
      }
    }
    setBusy(false);
    if (failed.length > 0) setError(`部分上传失败：\n${failed.join('\n')}`);
    await refresh();
  };

  const onDelete = async (docId: string): Promise<void> => {
    if (!window.confirm('删除该参考资料及其索引？')) return;
    try {
      await ipc.kbDelete(docId);
      await refresh();
    } catch (e) {
      setError(`删除失败 · ${String(e)}`);
    }
  };

  // 点击已上传文档→预览复制入库的源文件（md/txt/html 内置预览，office 走渲染，pdf/doc 走系统程序）
  const onPreview = async (doc: KbDocSummary): Promise<void> => {
    if (!doc.file_path) {
      setError('该文档没有可预览的源文件（可能未复制入库或文件已丢失）');
      return;
    }
    try {
      await previewLocalFile(doc.file_path);
    } catch (e) {
      setError(`预览失败 · ${String(e)}`);
    }
  };

  const onReindex = async (): Promise<void> => {
    try {
      const r = await ipc.kbReindex();
      if (r.ok) {
        setReindexing(true);
        await refresh();
      }
    } catch (e) {
      setError(`重建索引失败 · ${String(e)}`);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden" style={{ backgroundColor: '#ffffff' }}>
      {/* 头部 */}
      <div
        className="flex flex-shrink-0 items-center justify-between border-b px-4 py-3"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
      >
        <div className="min-w-0">
          <div className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
            知识库 / 参考资料
          </div>
          <div className="mt-0.5 text-2xs" style={{ color: '#616161' }}>
            上传法规/制度/案例等文件，分块 + 向量化入库；文档审核与聊天时经混合检索（BM25 + 向量 + RRF + 重排）自动引用。
          </div>
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => void onReindex()}
            disabled={reindexing}
            className="rounded px-2.5 py-1 text-2xs disabled:opacity-50"
            style={{ backgroundColor: '#ececec', color: '#333333' }}
            title="模型变更后基于库内原文重建向量索引"
          >
            {reindexing ? `重建中 ${Math.round((status?.reindex_progress ?? 0) * 100)}%` : '重建索引'}
          </button>
          <button
            type="button"
            onClick={() => navigate('/settings/knowledge')}
            className="rounded px-2.5 py-1 text-2xs"
            style={{ backgroundColor: '#ececec', color: '#333333' }}
            title="打开设置页调整 RAG 参数"
          >
            检索参数
          </button>
        </div>
      </div>

      {/* 上传条 */}
      <div
        className="flex flex-shrink-0 items-center gap-2 border-b px-4 py-2"
        style={{ borderColor: '#e0e0e0' }}
      >
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded border px-2 py-1 text-2xs outline-none"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff', color: '#1f1f1f' }}
          title="上传时打的分类标签（检索可按类过滤）"
        >
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => void onUpload()}
          disabled={busy}
          className="rounded px-3 py-1 text-2xs text-white disabled:opacity-50"
          style={{ backgroundColor: '#0e639c' }}
        >
          {busy ? '上传中…' : '＋ 上传参考资料'}
        </button>
        <span className="text-2xs" style={{ color: '#616161' }}>
          支持 pdf / docx / doc / txt / md / csv / html / xlsx / pptx（可多选）
        </span>
        {status && (
          <span className="ml-auto text-2xs" style={{ color: '#616161' }}>
            共 {status.stats.total_docs} 文档 · {status.stats.total_chunks} 分块 · 向量
            {status.embedding_available ? '就绪' : '不可用'} · 重排
            {status.reranker_available ? '就绪' : '未就绪'}
            {status.needs_reindex && <span style={{ color: '#b25c1a' }}> · 需重建</span>}
          </span>
        )}
      </div>

      {error && (
        <div
          className="mx-4 mt-2 flex-shrink-0 whitespace-pre-line rounded border px-3 py-2 text-2xs"
          style={{ borderColor: '#cd3131', color: '#cd3131', backgroundColor: '#fff5f5' }}
        >
          {error}
        </div>
      )}

      {/* 文件列表 */}
      <div className="flex-1 overflow-auto px-4 py-3">
        {docs.length === 0 ? (
          <div
            className="flex h-full items-center justify-center text-center text-ui"
            style={{ color: '#616161' }}
          >
            暂无参考资料。点击「＋ 上传参考资料」导入法规/制度/案例等文件，
            <br />
            之后文档审核与聊天会自动混合检索并引用其中内容。
          </div>
        ) : (
          <table className="w-full border-collapse text-2xs">
            <thead>
              <tr style={{ color: '#616161', borderBottom: '1px solid #e0e0e0' }}>
                <th className="px-2 py-1.5 text-left font-medium">文件</th>
                <th className="px-2 py-1.5 text-left font-medium">分类</th>
                <th className="px-2 py-1.5 text-left font-medium">状态</th>
                <th className="px-2 py-1.5 text-right font-medium">分块</th>
                <th className="px-2 py-1.5 text-right font-medium">大小</th>
                <th className="px-2 py-1.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => {
                const meta = STATUS_META[d.status] ?? { label: d.status, color: '#616161' };
                return (
                  <tr key={d.id} style={{ borderBottom: '1px solid #f0f0f0' }}>
                    <td className="px-2 py-1.5" style={{ color: '#1f1f1f' }}>
                      {d.file_path ? (
                        <button
                          type="button"
                          onClick={() => void onPreview(d)}
                          className="cursor-pointer text-left font-semibold underline decoration-dotted underline-offset-2 hover:text-[#0e639c]"
                          title={`${d.file_path}\n点击预览源文件`}
                          style={{ color: '#1f1f1f' }}
                        >
                          {d.file_name || d.title}
                        </button>
                      ) : (
                        <div className="font-semibold" title={d.file_name || d.title}>
                          {d.file_name || d.title}
                        </div>
                      )}
                      {d.status === 'failed' && d.error && (
                        <div className="mt-0.5" style={{ color: '#cd3131' }} title={d.error}>
                          {d.error}
                        </div>
                      )}
                    </td>
                    <td className="px-2 py-1.5" style={{ color: '#616161' }}>
                      {CATEGORIES.find((c) => c.value === d.category)?.label ?? d.category ?? '未分类'}
                    </td>
                    <td className="px-2 py-1.5" style={{ color: meta.color }}>
                      ● {meta.label}
                    </td>
                    <td className="px-2 py-1.5 text-right" style={{ color: '#616161' }}>
                      {d.chunk_count > 0 ? `${d.chunk_count} 块` : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right" style={{ color: '#616161' }}>
                      {fmtSize(d.size_bytes)}
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      <button
                        type="button"
                        onClick={() => void onPreview(d)}
                        disabled={!d.file_path}
                        className="mr-1 rounded px-2 py-0.5 disabled:opacity-40"
                        style={{ backgroundColor: '#ececec', color: '#0e639c' }}
                        title={d.file_path ? '预览源文件' : '无可预览的源文件'}
                      >
                        预览
                      </button>
                      <button
                        type="button"
                        onClick={() => void onDelete(d.id)}
                        className="rounded px-2 py-0.5"
                        style={{ backgroundColor: '#ececec', color: '#cd3131' }}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
