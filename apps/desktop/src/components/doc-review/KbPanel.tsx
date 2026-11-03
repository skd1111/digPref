/**
 * KbPanel —— 审核专家「知识库 / 参考资料」页（与「审批工作台」「文档审核」平级的顶层页签）。
 *
 * 上传的参考资料经 RAG 分块 + 向量化入库，文档审核与聊天时按混合检索
 * （SQLite FTS5 BM25 + sqlite-vec 向量 + RRF + ONNX reranker）召回注入提示词。
 * 上传走本地路径（Tauri 对话框/多选），文件复制入库，数据落安装目录 knowledge/，复制即迁移。
 */
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { useNavigate } from 'react-router-dom';
import { ipc } from '@/ipc/invoke';
import type { KbDocSummary, KbStatus, KbVersionItem } from '@/ipc/invoke';
import { previewLocalFile } from '@/store/officePreviewStore';
import { useDictOptions, type DictOption } from '@/hooks/useDictOptions';

const SUPPORTED_EXTENSIONS = [
  'pdf', 'docx', 'doc', 'txt', 'md', 'csv', 'html', 'htm', 'xlsx', 'pptx',
];

// 内置兜底（数据字典不可用时降级）
const FALLBACK_CATEGORIES: DictOption[] = [
  { key: 'finance', label: '财务/财税', value: 'finance', description: '' },
  { key: 'legal', label: '法务/合同', value: 'legal', description: '' },
  { key: 'compliance', label: '合规/制度', value: 'compliance', description: '' },
  { key: 'data_security', label: '数据安全', value: 'data_security', description: '' },
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

/** unix 秒 -> 本地时间短格式（版本历史上传时间）。 */
function fmtTime(ts: number): string {
  if (!ts) return '—';
  const dt = new Date(ts * 1000);
  const p = (x: number): string => String(x).padStart(2, '0');
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())} ${p(dt.getHours())}:${p(dt.getMinutes())}`;
}

export function KbPanel(): JSX.Element {
  const navigate = useNavigate();
  const [docs, setDocs] = useState<KbDocSummary[]>([]);
  const [status, setStatus] = useState<KbStatus | null>(null);
  const [category, setCategory] = useState('__auto__');
  const [busy, setBusy] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  // 搜索：输入框实时绑定 + debounce 300ms 后触发后端 FTS 检索
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  // 版本历史：当前展开的逻辑资料 group + 其全部版本（检索仅命中「生效」版本）
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
  const [versions, setVersions] = useState<KbVersionItem[]>([]);
  const [versionsBusy, setVersionsBusy] = useState(false);

  // 从数据字典动态读取知识库分类（实时生效）；不可用时降级到内置兜底
  const { options: dictCats } = useDictOptions('知识库分类');
  const categories = dictCats.length > 0 ? dictCats : FALLBACK_CATEGORIES;

  // debounce：输入停顿 300ms 后更新 debouncedQuery 触发 refresh
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQuery(searchQuery), 300);
    return () => window.clearTimeout(t);
  }, [searchQuery]);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const [list, st] = await Promise.all([
        ipc.kbList(debouncedQuery ? { q: debouncedQuery } : undefined),
        ipc.kbStatus(),
      ]);
      setDocs(list.docs ?? []);
      setStatus(st);
      setReindexing(st.reindexing);
    } catch (e) {
      setError(`读取知识库失败 · ${String(e)}`);
    }
  }, [debouncedQuery]);

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

  // 展开的版本组内有版本在索引 → 轮询版本历史 + 主列表，直到落定
  // （新版本索引期间 is_active=0 不入主列表，故需单独轮询以在就绪生效后刷新）
  useEffect(() => {
    const anchor = versions[0]?.doc_id;
    const busyVersions =
      expandedGroup !== null &&
      !!anchor &&
      versions.some((v) => v.status === 'indexing' || v.status === 'pending');
    if (!busyVersions || !anchor) return;
    const t = window.setInterval(() => {
      void (async () => {
        try {
          const r = await ipc.kbVersions(anchor);
          setVersions(r.versions ?? []);
          await refresh();
        } catch {
          /* 轮询失败静默，下一轮重试 */
        }
      })();
    }, 1500);
    return () => window.clearInterval(t);
  }, [expandedGroup, versions, refresh]);

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
        await ipc.kbUpload(p, category === '__auto__' ? '' : category);
      } catch (e) {
        failed.push(`${p.split(/[\\/]/).pop()}: ${String(e)}`);
      }
    }
    setBusy(false);
    if (failed.length > 0) setError(`部分上传失败：\n${failed.join('\n')}`);
    await refresh();
  };

  const onDelete = async (doc: KbDocSummary): Promise<void> => {
    const n =
      (doc.version_count ?? 1) > 1 ? `及其全部 ${doc.version_count} 个版本` : '及其索引';
    if (!window.confirm(`删除该参考资料${n}？`)) return;
    try {
      await ipc.kbDelete(doc.id, true);
      if (expandedGroup === doc.group_id) {
        setExpandedGroup(null);
        setVersions([]);
      }
      await refresh();
    } catch (e) {
      setError(`删除失败 · ${String(e)}`);
    }
  };

  // 加载并展开某资料的版本历史（用组内任一 doc_id 查，后端按 group 归并）
  const loadVersions = async (docId: string, group: string): Promise<void> => {
    setVersionsBusy(true);
    try {
      const r = await ipc.kbVersions(docId);
      setVersions(r.versions ?? []);
      setExpandedGroup(group);
    } catch (e) {
      setError(`读取版本历史失败 · ${String(e)}`);
    }
    setVersionsBusy(false);
  };

  const onToggleVersions = async (doc: KbDocSummary): Promise<void> => {
    if (expandedGroup === doc.group_id) {
      setExpandedGroup(null);
      setVersions([]);
      return;
    }
    await loadVersions(doc.id, doc.group_id);
  };

  // 更新版本：选一个新文件作为该资料的下一版本（索引期间旧版本仍生效，就绪后自动切换）
  const onUpdateVersion = async (doc: KbDocSummary): Promise<void> => {
    setError(null);
    let picked: string | string[] | null = null;
    try {
      picked = await open({
        multiple: false,
        filters: [{ name: '参考资料', extensions: SUPPORTED_EXTENSIONS }],
      });
    } catch (e) {
      setError(`打开文件对话框失败 · ${String(e)}`);
      return;
    }
    if (!picked) return;
    const path = Array.isArray(picked) ? picked[0] : picked;
    if (!SUPPORTED_EXTENSIONS.includes(extOf(path))) {
      setError('没有受支持的文件（pdf/docx/doc/txt/md/csv/html/xlsx/pptx）');
      return;
    }
    setBusy(true);
    try {
      await ipc.kbUpload(path, doc.category, doc.id);
      await loadVersions(doc.id, doc.group_id); // 展开以观察新版本索引进度
    } catch (e) {
      setError(`更新版本失败 · ${String(e)}`);
    }
    setBusy(false);
    await refresh();
  };

  // 设为生效：把某个（可能是旧的）版本切为唯一生效版本（O(1)、无需重建索引）
  const onActivate = async (versionDocId: string, group: string): Promise<void> => {
    try {
      await ipc.kbActivate(versionDocId);
      await loadVersions(versionDocId, group);
      await refresh();
    } catch (e) {
      setError(`切换生效版本失败 · ${String(e)}`);
    }
  };

  // 删除单个版本（其余版本保留；若删的是生效版本，后端自动切最新剩余）
  const onDeleteVersion = async (versionDocId: string, group: string): Promise<void> => {
    if (!window.confirm('删除该版本？（同资料其余版本保留）')) return;
    try {
      await ipc.kbDelete(versionDocId, false);
      const survivors = versions.filter((v) => v.doc_id !== versionDocId);
      if (survivors.length > 0) {
        await loadVersions(survivors[0].doc_id, group);
      } else {
        setExpandedGroup(null);
        setVersions([]);
      }
      await refresh();
    } catch (e) {
      setError(`删除版本失败 · ${String(e)}`);
    }
  };

  // 预览某个版本的源文件
  const onPreviewVersion = async (v: KbVersionItem): Promise<void> => {
    if (!v.file_path) {
      setError('该版本没有可预览的源文件（可能未复制入库或文件已丢失）');
      return;
    }
    try {
      await previewLocalFile(v.file_path);
    } catch (e) {
      setError(`预览失败 · ${String(e)}`);
    }
  };

  // 修改分类（作用于整份资料的全部版本，后端同步刷新子块 category）
  const onChangeCategory = async (doc: KbDocSummary, category: string): Promise<void> => {
    setError(null);
    try {
      await ipc.kbSetCategory(doc.id, category);
      await refresh();
      if (expandedGroup === doc.group_id) await loadVersions(doc.id, doc.group_id);
    } catch (e) {
      setError(`修改分类失败 · ${String(e)}`);
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
          <option value="__auto__">自动识别</option>
          <option value="">未分类</option>
          {categories.map((c) => (
            <option key={c.key} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        {/* 搜索框 */}
        <div className="relative">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索文档名称…"
            className="rounded border py-1 pl-2 pr-6 text-2xs outline-none"
            style={{
              borderColor: debouncedQuery ? '#0e639c' : '#d4d4d4',
              backgroundColor: '#ffffff',
              color: '#1f1f1f',
              width: '180px',
            }}
            title="按文档标题 / 文件名搜索（支持中文分词）"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute right-1 top-1/2 -translate-y-1/2 rounded px-1 text-2xs"
              style={{ color: '#616161' }}
              title="清除搜索"
            >
              ✕
            </button>
          )}
        </div>
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

      {/* 搜索结果提示条 */}
      {debouncedQuery && (
        <div
          className="flex flex-shrink-0 items-center gap-1 border-b px-4 py-1.5 text-2xs"
          style={{ borderColor: '#e0e0e0', backgroundColor: '#f8f9fa', color: '#333' }}
        >
          <span>
            搜索 “{debouncedQuery}” — 找到 {docs.length} 个文档
          </span>
          <button
            type="button"
            onClick={() => setSearchQuery('')}
            className="rounded px-1.5 py-0.5"
            style={{ color: '#0e639c' }}
          >
            清除搜索
          </button>
        </div>
      )}

      {/* 文件列表 */}
      <div className="flex-1 overflow-auto px-4 py-3">
        {docs.length === 0 ? (
          <div
            className="flex h-full items-center justify-center text-center text-ui"
            style={{ color: '#616161' }}
          >
            {debouncedQuery ? (
              <>
                未找到匹配“{debouncedQuery}”的文档。
                <br />
                试试其他关键词，或
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="mx-1 underline"
                  style={{ color: '#0e639c' }}
                >
                  清除搜索
                </button>
                查看全部文档。
              </>
            ) : (
              <>
                暂无参考资料。点击「＋ 上传参考资料」导入法规/制度/案例等文件，
                <br />
                之后文档审核与聊天会自动混合检索并引用其中内容。
              </>
            )}
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
                const vc = d.version_count ?? 1;
                const expanded = expandedGroup !== null && expandedGroup === d.group_id;
                return (
                  <Fragment key={d.id}>
                    <tr style={{ borderBottom: '1px solid #f0f0f0' }}>
                      <td className="px-2 py-1.5" style={{ color: '#1f1f1f' }}>
                        <div className="flex items-center gap-1.5">
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
                            <span className="font-semibold" title={d.file_name || d.title}>
                              {d.file_name || d.title}
                            </span>
                          )}
                          {vc > 1 && (
                            <span
                              className="rounded px-1.5 py-0.5 text-2xs"
                              style={{ backgroundColor: '#eef3f8', color: '#0e639c' }}
                              title={`该资料共 ${vc} 个版本，当前生效 v${d.version}`}
                            >
                              v{d.version} · 共{vc}版
                            </span>
                          )}
                        </div>
                        {d.status === 'failed' && d.error && (
                          <div className="mt-0.5" style={{ color: '#cd3131' }} title={d.error}>
                            {d.error}
                          </div>
                        )}
                      </td>
                      <td className="px-2 py-1.5" style={{ color: '#616161' }}>
                        <select
                          value={d.category ?? ''}
                          onChange={(e) => void onChangeCategory(d, e.target.value)}
                          className="rounded border px-1.5 py-0.5 text-2xs outline-none"
                          style={{
                            borderColor: '#d4d4d4',
                            backgroundColor: '#ffffff',
                            color: '#333333',
                          }}
                          title="修改该资料的分类（作用于全部版本，并同步用于按类检索）"
                        >
                          {categories.some((c) => c.value === (d.category ?? '')) ? null : (
                            <option value={d.category ?? ''}>{d.category || '未分类'}</option>
                          )}
                          <option value="">未分类</option>
                          {categories.map((c) => (
                            <option key={c.key} value={c.value}>
                              {c.label}
                            </option>
                          ))}
                        </select>
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
                      <td className="px-2 py-1.5 text-right whitespace-nowrap">
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
                          onClick={() => void onUpdateVersion(d)}
                          disabled={busy}
                          className="mr-1 rounded px-2 py-0.5 disabled:opacity-40"
                          style={{ backgroundColor: '#ececec', color: '#0e639c' }}
                          title="上传新文件作为该资料的新版本（就绪后自动生效，旧版本保留）"
                        >
                          更新版本
                        </button>
                        <button
                          type="button"
                          onClick={() => void onToggleVersions(d)}
                          className="mr-1 rounded px-2 py-0.5"
                          style={{
                            backgroundColor: expanded ? '#0e639c' : '#ececec',
                            color: expanded ? '#ffffff' : '#333333',
                          }}
                          title="查看/切换该资料的历史版本"
                        >
                          {expanded ? '收起版本' : `版本历史${vc > 1 ? ` ${vc}` : ''}`}
                        </button>
                        <button
                          type="button"
                          onClick={() => void onDelete(d)}
                          className="rounded px-2 py-0.5"
                          style={{ backgroundColor: '#ececec', color: '#cd3131' }}
                          title={vc > 1 ? `删除该资料及其全部 ${vc} 个版本` : '删除该参考资料及其索引'}
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                    {expanded && (
                      <tr style={{ borderBottom: '1px solid #f0f0f0', backgroundColor: '#fafcff' }}>
                        <td colSpan={6} className="px-3 py-2">
                          {versionsBusy ? (
                            <div className="text-2xs" style={{ color: '#616161' }}>
                              加载版本历史…
                            </div>
                          ) : (
                            <div>
                              <div
                                className="mb-1 text-2xs font-semibold"
                                style={{ color: '#333333' }}
                              >
                                版本历史（{versions.length}）· 检索仅使用标记为「生效」的版本
                              </div>
                              <table className="w-full border-collapse text-2xs">
                                <tbody>
                                  {versions.map((v) => {
                                    const vm =
                                      STATUS_META[v.status] ?? { label: v.status, color: '#616161' };
                                    return (
                                      <tr
                                        key={v.doc_id}
                                        style={{ borderBottom: '1px solid #eef0f2' }}
                                      >
                                        <td className="px-2 py-1" style={{ color: '#1f1f1f' }}>
                                          <span className="font-semibold">v{v.version}</span>
                                          {v.is_active ? (
                                            <span
                                              className="ml-1.5 rounded px-1 py-0.5"
                                              style={{ backgroundColor: '#e7f6ec', color: '#059669' }}
                                            >
                                              ● 生效
                                            </span>
                                          ) : (
                                            <span className="ml-1.5" style={{ color: '#9a9a9a' }}>
                                              ○ 已归档
                                            </span>
                                          )}
                                          <span className="ml-1.5" style={{ color: '#9a9a9a' }}>
                                            {v.file_name}
                                          </span>
                                        </td>
                                        <td className="px-2 py-1" style={{ color: vm.color }}>
                                          ● {vm.label}
                                        </td>
                                        <td
                                          className="px-2 py-1 text-right"
                                          style={{ color: '#616161' }}
                                        >
                                          {v.chunk_count > 0 ? `${v.chunk_count} 块` : '—'}
                                        </td>
                                        <td
                                          className="px-2 py-1 text-right"
                                          style={{ color: '#616161' }}
                                        >
                                          {fmtSize(v.size_bytes)}
                                        </td>
                                        <td
                                          className="px-2 py-1 text-right"
                                          style={{ color: '#616161' }}
                                        >
                                          {fmtTime(v.created_at)}
                                        </td>
                                        <td className="px-2 py-1 text-right whitespace-nowrap">
                                          <button
                                            type="button"
                                            onClick={() => void onPreviewVersion(v)}
                                            disabled={!v.file_path}
                                            className="mr-1 rounded px-2 py-0.5 disabled:opacity-40"
                                            style={{ backgroundColor: '#ececec', color: '#0e639c' }}
                                          >
                                            预览
                                          </button>
                                          <button
                                            type="button"
                                            onClick={() => void onActivate(v.doc_id, d.group_id)}
                                            disabled={v.is_active || v.status !== 'ready'}
                                            className="mr-1 rounded px-2 py-0.5 disabled:opacity-40"
                                            style={{ backgroundColor: '#e7f6ec', color: '#059669' }}
                                            title={
                                              v.is_active
                                                ? '当前生效版本'
                                                : v.status !== 'ready'
                                                  ? '仅就绪版本可设为生效'
                                                  : '把此版本设为检索使用的生效版本'
                                            }
                                          >
                                            设为生效
                                          </button>
                                          <button
                                            type="button"
                                            onClick={() =>
                                              void onDeleteVersion(v.doc_id, d.group_id)
                                            }
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
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
