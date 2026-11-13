/**
 * KbPanel —— 审核专家「知识库 / 参考资料」页（与「审批工作台」「文档审核」平级的顶层页签）。
 *
 * 上传走本地路径（Tauri 对话框/多选），文件复制入库，数据落安装目录 knowledge/，复制即迁移。
 */
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { useNavigate } from 'react-router-dom';
import { ipc } from '@/ipc/invoke';
import type { KbDocSummary, KbStatus, KbVersionItem } from '@/ipc/invoke';
import { previewLocalFile } from '@/store/officePreviewStore';
import { useDictOptions, type DictOption } from '@/hooks/useDictOptions';
import { CloseIcon } from '@/components/ui/Icons';
import { KbImportDialog, type PendingImport } from '@/components/doc-review/KbImportDialog';

const SUPPORTED_EXTENSIONS = [
  'pdf', 'docx', 'doc', 'txt', 'md', 'csv', 'html', 'htm', 'xlsx', 'pptx',
];

// 内置兜底（数据字典不可用时降级）；与后端 datadict 种子「知识库分类」保持一致
const FALLBACK_CATEGORIES: DictOption[] = [
  { key: 'finance', label: '财务/财税', value: 'finance', description: '' },
  { key: 'legal', label: '法务/合同', value: 'legal', description: '' },
  { key: 'compliance', label: '合规/制度', value: 'compliance', description: '' },
  { key: 'data_security', label: '数据安全', value: 'data_security', description: '' },
  { key: 'hr', label: '人力/组织', value: 'hr', description: '' },
  { key: 'operations', label: '运营/业务', value: 'operations', description: '' },
  { key: 'risk', label: '风险/信贷', value: 'risk', description: '' },
  { key: 'marketing', label: '营销/品牌', value: 'marketing', description: '' },
  { key: 'engineering', label: '技术/研发', value: 'engineering', description: '' },
];

/** 分类预判分批：每批文件数（云端模型限流时小批量更稳） */
const CLASSIFY_BATCH_SIZE = 5;
/** 某批失败（限流）后下一批的退避等待（ms） */
const CLASSIFY_BATCH_BACKOFF_MS = 3000;
/** 分类筛选下拉的「全部」哨兵（后端传空串等于不过滤，故不能用 ''） */
const CATEGORY_ALL = '__all__';

/** 阻塞等待（分批之间的让速/退避用） */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/** 把后端原始错误翻译成可读中文（限流/超时/连接失败等常见原因）。 */
function friendlyError(raw: unknown): string {
  const text = String(raw ?? '');
  if (/429|rate.?limit|速率限制|限流/i.test(text)) {
    return '模型限流（Token Plan 速率限制）：请稍后重试；批量导入请在「检索参数」里把「入库并发数」调小到 1，或恢复本地模型';
  }
  if (/timeout|timed out|超时/i.test(text)) {
    return '请求超时：Agent 繁忙或模型响应慢，请稍后重试';
  }
  if (/connect|连接|refused|网络/i.test(text)) {
    return '无法连接 Agent：请确认 Agent 进程在运行（状态栏应显示「Agent: 未活动」或「Agent: 运行中」）';
  }
  return text;
}

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: '待索引', color: '#616161' },
  queued: { label: '排队中', color: '#616161' },
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
  // 分类筛选（只看该分类下的文档）；'__all__' = 全部
  const [filterCategory, setFilterCategory] = useState(CATEGORY_ALL);
  const [importing, setImporting] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  // 刷新并发闸门：为 true 时丢弃重入，避免慢响应下轮询请求叠加成风暴
  const inflightRef = useRef(false);
  // 搜索：输入框实时绑定 + debounce 300ms 后触发后端 FTS 检索
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  // 版本历史：当前展开的逻辑资料 group + 其全部版本（检索仅命中「生效」版本）
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
  const [versions, setVersions] = useState<KbVersionItem[]>([]);
  const [versionsBusy, setVersionsBusy] = useState(false);
  // 批量选择：主列表按 doc_id 勾选，配合「批量删除」（连同各自全部版本）
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  // 表头全选框（半选态需用 DOM property 表达）
  const headCheckRef = useRef<HTMLInputElement>(null);
  // 导入确认：待导入清单（尚未入库）+ 弹窗开关 + 更新版本时的基准资料
  const [pending, setPending] = useState<PendingImport[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [importBase, setImportBase] = useState<KbDocSummary | null>(null);
  // 导入前分类预判：识别中 / 提示（未开启、部分失败）
  const [classifying, setClassifying] = useState(false);
  const [classifyHint, setClassifyHint] = useState('');

  // 从数据字典动态读取知识库分类（实时生效）；不可用时降级到内置兜底
  const { options: dictCats } = useDictOptions('知识库分类');
  const categories = dictCats.length > 0 ? dictCats : FALLBACK_CATEGORIES;

  // debounce：输入停顿 300ms 后更新 debouncedQuery 触发 refresh
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQuery(searchQuery), 300);
    return () => window.clearTimeout(t);
  }, [searchQuery]);

  const refresh = useCallback(async (opts?: { silent?: boolean }): Promise<void> => {
    // 并发闸门：导入高峰期后端响应变慢，1.5s 定时器会与未完成的重叠请求叠加成请求风暴，
    // 反过来打满 Agent 单 worker 触发 read_body 截断。这里丢弃重入，保证同一时刻只有一组在飞。
    if (inflightRef.current) return;
    inflightRef.current = true;
    // 搜索 + 分类筛选一起下推后端；「全部」不传 category
    const listParams: { q?: string; category?: string } = {};
    if (debouncedQuery) listParams.q = debouncedQuery;
    if (filterCategory !== CATEGORY_ALL) listParams.category = filterCategory;
    try {
      const [list, st] = await Promise.all([
        ipc.kbList(listParams),
        ipc.kbStatus(),
      ]);
      setDocs(list.docs ?? []);
      setStatus(st);
      setReindexing(st.reindexing);
      setError(null);
    } catch (e) {
      // 后台轮询遇瞬时错误（如 read_body 截断）静默跳过、保留上次数据，下一轮自动重试；
      // 仅用户主动/初始化刷新才提示红字，避免偶发抖动误报「读取知识库失败」。
      if (!opts?.silent) setError(`读取知识库失败 · ${friendlyError(e)}`);
    } finally {
      inflightRef.current = false;
    }
  }, [debouncedQuery, filterCategory]);

  // 列表变化（搜索/刷新）后裁剪选中集：只保留仍在列表中的条目
  useEffect(() => {
    setSelected((prev) => {
      if (prev.size === 0) return prev;
      const ids = new Set(docs.map((d) => d.id));
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [docs]);

  // 全选框半选态：部分选中时显示 indeterminate
  useEffect(() => {
    if (headCheckRef.current) {
      headCheckRef.current.indeterminate = selected.size > 0 && selected.size < docs.length;
    }
  }, [selected.size, docs.length]);

  // 单条勾选 / 全选
  const toggleOne = (id: string): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = (checked: boolean): void => {
    setSelected(checked ? new Set(docs.map((d) => d.id)) : new Set());
  };

  // 有文档在索引中或正在重建 → 轮询，直到落定
  useEffect(() => {
    const busyNow =
      reindexing ||
      docs.some((d) => d.status === 'indexing' || d.status === 'pending' || d.status === 'queued');
    if (busyNow && pollRef.current === null) {
      pollRef.current = window.setInterval(() => void refresh({ silent: true }), 1500);
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
      versions.some(
        (v) => v.status === 'indexing' || v.status === 'pending' || v.status === 'queued',
      );
    if (!busyVersions || !anchor) return;
    const t = window.setInterval(() => {
      void (async () => {
        try {
          const r = await ipc.kbVersions(anchor);
          setVersions(r.versions ?? []);
          await refresh({ silent: true });
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

  // 导入前自动分类预判（只读，不写库）：把判定结果回填到待导入清单。
  // 正式导入时直接带上已判定的分类 → 入库阶段不再重复调用分类（省一次模型调用）。
  // 条数多时分批调用（默认 5 个/批）：避免一次把模型打爆触发 429 限流；
  // 某批出现失败（多为限流）时下一批先退避，给后端恢复窗口。
  const runAutoClassify = async (list: PendingImport[]): Promise<void> => {
    if (list.length === 0) return;
    setClassifying(true);
    setClassifyHint('');
    const batches: PendingImport[][] = [];
    for (let i = 0; i < list.length; i += CLASSIFY_BATCH_SIZE) {
      batches.push(list.slice(i, i + CLASSIFY_BATCH_SIZE));
    }
    const byPath = new Map<string, { category: string; label: string; error?: string; source?: string }>();
    let failed = 0;
    let disabled = false;
    try {
      for (let bi = 0; bi < batches.length; bi++) {
        const batch = batches[bi];
        setClassifyHint(
          `正在识别分类…（第 ${bi + 1}/${batches.length} 批，共 ${list.length} 个文件）`,
        );
        let res: Awaited<ReturnType<typeof ipc.kbClassifyPreview>>;
        try {
          res = await ipc.kbClassifyPreview(batch.map((it) => it.path));
        } catch (e) {
          failed += batch.length;
          setClassifyHint(`第 ${bi + 1} 批识别失败：${friendlyError(e)}`);
          await sleep(CLASSIFY_BATCH_BACKOFF_MS);
          continue;
        }
        if (!res.enabled) {
          disabled = true;
          setClassifyHint(res.reason ?? '自动分类未启用，导入时不会自动判定分类');
          break; // 开关未开 → 后续批次无需继续
        }
        for (const it of res.items ?? []) byPath.set(it.path, it);
        const batchFailed = (res.items ?? []).filter((it) => !it.category).length;
        failed += batchFailed;
        applyClassifyResults(byPath);
        if (batchFailed > 0) {
          // 本批有失败（多为限流）→ 下一批先退避，避免继续加压
          await sleep(CLASSIFY_BATCH_BACKOFF_MS);
        }
      }
      applyClassifyResults(byPath);
      if (!disabled && failed > 0) {
        setClassifyHint(
          `${failed} 个文件未能判定分类（模型限流或不可用）· 可点「重试分类」或手动选择`,
        );
      }
    } catch (e) {
      setClassifyHint(`自动分类预判失败：${friendlyError(e)}`);
    } finally {
      setClassifying(false);
    }
  };

  // 把预判结果回填到待导入清单（已判定的即时可见；用户手改的不覆盖）
  const applyClassifyResults = (
    byPath: Map<string, { category: string; label: string; error?: string; source?: string }>,
  ): void => {
    if (byPath.size === 0) return;
    setPending((prev) =>
      prev.map((it) => {
        if (it.category !== '__auto__') return it; // 用户已手动指定 → 不覆盖
        const hit = byPath.get(it.path);
        if (!hit) return it;
        if (hit.category) {
          return {
            ...it,
            category: hit.category,
            detected: hit.source === 'heuristic' ? 'heuristic' : 'llm',
            error: undefined,
          };
        }
        return { ...it, error: hit.error || '未能判定分类' };
      }),
    );
  };

  // 重试分类：只对仍未判定的条目再跑一次预判（已判定的不重复消耗模型）
  const retryAutoClassify = async (): Promise<void> => {
    const todo = pending.filter((it) => it.category === '__auto__');
    if (todo.length === 0) return;
    await runAutoClassify(todo);
  };

  // 选择文件 → 只生成待导入清单，确认弹窗里点「确认导入」后才入库 + 向量化
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
    const now = Date.now();
    const list: PendingImport[] = supported.map((p, i) => ({
      key: `imp-${now}-${i}`,
      path: p,
      name: p.split(/[\\/]/).pop() ?? p,
      // 分类统一交给导入弹窗的「自动识别」（模型预判），可逐条手改
      category: '__auto__',
      addedAt: now,
    }));
    setPending(list);
    setImportBase(null);
    setImportOpen(true);
    void runAutoClassify(list);
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

  // 批量删除：逐条删除选中的资料（语义同单条删除 → 含各自全部版本），失败项汇总提示
  const onBatchDelete = async (): Promise<void> => {
    const targets = docs.filter((d) => selected.has(d.id));
    if (targets.length === 0) return;
    const names = targets.map((d) => d.file_name || d.title).join('、');
    const shown = names.length > 60 ? `${names.slice(0, 60)}…` : names;
    if (
      !window.confirm(
        `确认删除选中的 ${targets.length} 份参考资料（含各自全部版本）？\n${shown}\n该操作不可撤销。`,
      )
    ) {
      return;
    }
    setError(null);
    setDeleting(true);
    const failed: string[] = [];
    for (const d of targets) {
      try {
        await ipc.kbDelete(d.id, true);
      } catch (e) {
        failed.push(`${d.file_name || d.title}: ${String(e)}`);
      }
    }
    setDeleting(false);
    setSelected(new Set());
    if (expandedGroup && targets.some((d) => d.group_id === expandedGroup)) {
      setExpandedGroup(null);
      setVersions([]);
    }
    if (failed.length > 0) setError(`部分删除失败：\n${failed.join('\n')}`);
    await refresh();
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
  // 与新增资料一致：先出待导入清单，确认后才入库 + 向量化
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
    const now = Date.now();
    const list: PendingImport[] = [
      {
        key: `imp-${now}-0`,
        path,
        name: path.split(/[\\/]/).pop() ?? path,
        category: doc.category ?? '',
        addedAt: now,
        baseDocId: doc.id,
      },
    ];
    setPending(list);
    setImportBase(doc);
    setImportOpen(true);
    void runAutoClassify(list);
  };

  // 确认导入：逐条入库（复制源文件 → 解析 → 分块 → 向量化由后端后台跑）
  const onConfirmImport = async (): Promise<void> => {
    if (pending.length === 0) return;
    setImporting(true);
    const failed: string[] = [];
    for (const item of pending) {
      try {
        await ipc.kbUpload(
          item.path,
          item.category === '__auto__' ? '' : item.category,
          item.baseDocId,
        );
      } catch (e) {
        failed.push(`${item.name}: ${friendlyError(e)}`);
      }
    }
    setImporting(false);
    const base = importBase;
    setPending([]);
    setImportBase(null);
    setImportOpen(false);
    if (failed.length > 0) setError(`部分导入失败：\n${failed.join('\n')}`);
    await refresh();
    if (base) {
      try {
        await loadVersions(base.id, base.group_id); // 展开以观察新版本索引进度
      } catch {
        /* 展开失败静默：导入本身已成功 */
      }
    }
  };

  // 取消导入：丢弃待导入清单（不入库、不向量化）
  const onCancelImport = (): void => {
    setPending([]);
    setImportBase(null);
    setImportOpen(false);
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
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => void onReindex()}
            disabled={reindexing}
            className="btn btn-secondary btn-sm"
            title="模型变更后基于库内原文重建向量索引"
          >
            {reindexing ? `重建中 ${Math.round((status?.reindex_progress ?? 0) * 100)}%` : '重建索引'}
          </button>
          <button
            type="button"
            onClick={() => navigate('/settings/knowledge')}
            className="btn btn-secondary btn-sm"
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
        {/* 分类筛选：选中某分类只列该分类下的文档 */}
        <select
          value={filterCategory}
          onChange={(e) => {
            setFilterCategory(e.target.value);
            // 筛选后列表会变，收起已展开的版本历史
            setExpandedGroup(null);
            setVersions([]);
          }}
          className="rounded border px-2 py-1 text-2xs outline-none"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff', color: '#1f1f1f' }}
          title="按分类筛选文档"
        >
          <option value={CATEGORY_ALL}>全部</option>
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
              className="btn btn-ghost btn-icon absolute right-1 top-1/2 -translate-y-1/2"
              title="清除搜索"
              aria-label="清除搜索"
            >
              <CloseIcon size={12} />
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={() => void onUpload()}
          disabled={importing}
          className="btn btn-primary btn-sm"
        >
          {importing ? '导入中…' : '＋ 上传参考资料'}
        </button>
        <button
          type="button"
          onClick={() => void onBatchDelete()}
          disabled={selected.size === 0 || deleting}
          className="btn btn-danger btn-sm"
          title="删除选中的参考资料（连同各自的全部版本），不可撤销"
        >
          {deleting ? '删除中…' : `批量删除${selected.size > 0 ? ` (${selected.size})` : ''}`}
        </button>
        <span className="text-2xs" style={{ color: '#616161' }}>
          支持 pdf / docx / doc / txt / md / csv / html / xlsx / pptx（可多选）
        </span>
        {status && (
          <span className="ml-auto text-2xs" style={{ color: '#616161' }}>
            共 {status.stats.total_docs} 文档
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
            className="btn btn-ghost btn-xs"
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
                之后文档审核与聊天会自动引用其中内容。
              </>
            )}
          </div>
        ) : (
          <table className="w-full border-collapse text-2xs">
            <thead>
              <tr style={{ color: '#616161', borderBottom: '1px solid #e0e0e0' }}>
                <th className="w-6 px-2 py-1.5 text-left font-medium">
                  <input
                    ref={headCheckRef}
                    type="checkbox"
                    checked={docs.length > 0 && selected.size === docs.length}
                    onChange={(e) => toggleAll(e.target.checked)}
                    title="全选 / 取消全选"
                    aria-label="全选"
                    className="cursor-pointer"
                  />
                </th>
                <th className="px-2 py-1.5 text-left font-medium">文件</th>
                <th className="px-2 py-1.5 text-left font-medium">分类</th>
                <th className="px-2 py-1.5 text-left font-medium">状态</th>
                <th className="px-2 py-1.5 text-right font-medium">分块</th>
                <th className="px-2 py-1.5 text-right font-medium">大小</th>
                <th className="px-2 py-1.5 text-right font-medium">导入时间</th>
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
                      <td className="px-2 py-1.5">
                        <input
                          type="checkbox"
                          checked={selected.has(d.id)}
                          onChange={() => toggleOne(d.id)}
                          title={`选择 ${d.file_name || d.title}`}
                          aria-label={`选择 ${d.file_name || d.title}`}
                          className="cursor-pointer"
                        />
                      </td>
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
                      <td
                        className="px-2 py-1.5 text-right whitespace-nowrap"
                        style={{ color: '#616161' }}
                      >
                        {fmtTime(d.created_at)}
                      </td>
                      <td className="px-2 py-1.5 text-right whitespace-nowrap">
                        <button
                          type="button"
                          onClick={() => void onPreview(d)}
                          disabled={!d.file_path}
                          className="mr-1 btn btn-secondary btn-xs"
                          title={d.file_path ? '预览源文件' : '无可预览的源文件'}
                        >
                          预览
                        </button>
                        <button
                          type="button"
                          onClick={() => void onUpdateVersion(d)}
                          disabled={importing}
                          className="mr-1 btn btn-secondary btn-xs"
                          title="上传新文件作为该资料的新版本（就绪后自动生效，旧版本保留）"
                        >
                          更新版本
                        </button>
                        <button
                          type="button"
                          onClick={() => void onToggleVersions(d)}
                          className={`mr-1 btn btn-xs ${expanded ? 'btn-primary' : 'btn-secondary'}`}
                          title="查看/切换该资料的历史版本"
                        >
                          {expanded ? '收起版本' : `版本历史${vc > 1 ? ` ${vc}` : ''}`}
                        </button>
                        <button
                          type="button"
                          onClick={() => void onDelete(d)}
                          className="btn btn-danger btn-xs"
                          title={vc > 1 ? `删除该资料及其全部 ${vc} 个版本` : '删除该参考资料及其索引'}
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                    {expanded && (
                      <tr style={{ borderBottom: '1px solid #f0f0f0', backgroundColor: '#fafcff' }}>
                        <td colSpan={8} className="px-3 py-2">
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
                                            className="mr-1 btn btn-secondary btn-xs"
                                          >
                                            预览
                                          </button>
                                          <button
                                            type="button"
                                            onClick={() => void onActivate(v.doc_id, d.group_id)}
                                            disabled={v.is_active || v.status !== 'ready'}
                                            className="mr-1 btn btn-success btn-xs"
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
                                            className="btn btn-danger btn-xs"
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

      {/* 导入确认弹窗：确认后才真正入库（分块 + 向量化） */}
      {importOpen && (
        <KbImportDialog
          items={pending}
          categories={categories}
          busy={importing}
          mode={importBase ? 'version' : 'new'}
          onChangeCategory={(key, cat) =>
            setPending((prev) =>
              prev.map((it) =>
                it.key === key ? { ...it, category: cat, detected: undefined, error: undefined } : it,
              ),
            )
          }
          onRemove={(key) => setPending((prev) => prev.filter((it) => it.key !== key))}
          classifying={classifying}
          hint={classifyHint}
          onRetry={() => void retryAutoClassify()}
          onCancel={onCancelImport}
          onConfirm={() => void onConfirmImport()}
        />
      )}
    </div>
  );
}
