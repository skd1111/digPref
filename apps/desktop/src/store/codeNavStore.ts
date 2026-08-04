/**
 * codeNavStore —— Phase 2F 代码阅读与 AI 导航状态（V0 MVP，前端 mock 数据）。
 *
 * 范围：
 *   - 15-20 个 mock symbol（覆盖 Java/Python/TS + class/method/function/field 四 kind）
 *   - 搜索 / 过滤（kind / language）
 *   - 选中 symbol + AI 解释（mock）
 *   - 索引状态
 *
 * 后续 V1 接入时：
 *   - mock symbols 替换为 Tauri invoke('code_nav_jump') / 'code_nav_search' 返回
 *   - ai explanation 替换为后端 LLM 调用结果
 *   - zustand 接口保持兼容即可平滑切换
 */
import { create } from 'zustand';
import type {
  AiExplanation,
  IndexStatus,
  Language,
  Symbol,
  SymbolKind,
} from '@/types/codenav';

/**
 * 通过 Tauri command 读文件内容（前 4000 字符），失败返回空字符串。
 * 失败时 LLM 拿不到上下文，但能继续运行（mock 兜底）。
 */
async function readCurrentFileContext(filePath: string): Promise<string> {
  if (!filePath) return '';
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    // Tauri fs API：读文本文件
    const content = await invoke<string>('plugin:fs|read_text_file', {
      path: filePath,
    });
    return typeof content === 'string' ? content.slice(0, 4000) : '';
  } catch {
    return '';
  }
}

// ---------- AI 解释 mock ----------

const AI_EXPLANATION_TEMPLATES: Record<SymbolKind, string[]> = {
  class: [
    '该类封装了 `{name}` 的核心业务逻辑，包含 {n} 个公开方法。依赖 {deps}，生命周期由 Spring 容器管理。',
    '属于 `{parent}` 业务模块的入口类，遵循 Domain-Driven Design 聚合根模式。所有写操作必须经过 HITL 闸门。',
  ],
  method: [
    '`{name}` 是类 `{parent}` 的实例方法，时间复杂度 O(n)，需关注 N+1 查询风险。',
    '该方法涉及数据变更，已在 mcp-server-database 侧做只读 / 写操作分类。生产环境调用前需审批。',
  ],
  function: [
    '`{name}` 是顶层函数，处理 {n} 个分支。无副作用，可安全并发调用。',
    '该函数为 React 组件或工具函数，符合 EAIDE 内部编码规范。',
  ],
  interface: [
    '接口 `{name}` 定义了 {n} 个抽象方法，是 `{parent}` 模块对外契约。',
  ],
  field: [
    '字段 `{name}`，类型推断为常量或配置项。可在 Settings 面板修改。',
  ],
  enum: [
    '枚举 `{name}` 包含 {n} 个值，跨模块共享。',
  ],
};

// ---------- Store ----------

/** 已在 Monaco 中打开的代码文件（Tab） */
export interface CodeFile {
  path: string;
  content: string;
  language: string;
}

/** 由文件扩展名推断 Monaco language id */
function detectLanguage(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    java: 'java',
    py: 'python',
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    go: 'go',
    rs: 'rust',
    json: 'json',
    sql: 'sql',
    sh: 'shell',
    bash: 'shell',
    yml: 'yaml',
    yaml: 'yaml',
    md: 'markdown',
  };
  return map[ext] ?? 'plaintext';
}

interface CodeNavState {
  symbols: Symbol[];
  indexStatus: IndexStatus;
  search: string;
  filterKind: SymbolKind | 'all';
  filterLanguage: Language | 'all';
  selectedSymbolId: string | null;
  aiExplanation: AiExplanation | null;
  isExplaining: boolean;
  /** 后端 fetch 错误信息（null = 正常） */
  backendError: string | null;
  /** 跨文件跳转目标（CodeNavExtension 调用 openFileAtLine 设置） */
  pendingJump: { file_path: string; line: number } | null;
  /** 最近一次导入反馈（成功） */
  lastImport: { filePath: string; totalSymbols: number; at: number } | null;
  /** 最近一次导入反馈（失败） */
  lastImportError: { filePath: string; error: string; at: number } | null;
  /** 用户已打开的项目根目录列表（File → Open Folder） */
  openedProjects: string[];
  /** Phase 12 V1：附加到主对话的 Monaco 选中范围（用户在 Monaco 右键「📋 附加选区到对话」时设）。
   *  ChatInput 显示 chip + 发送时把代码拼到 user message 并写一条「system」提示词。
   *  auto=true 表示由编辑器选区变化自动同步；manual (false) 表示右键菜单手动附加。
   *  自动同步的选区在 cursor 变成单点（无选区）时会被清掉；手动附加的不会被自动清掉。 */
  chatSelection: {
    file: string;
    startLine: number;
    endLine: number;
    text: string;
    label: string;
    auto?: boolean;
  } | null;
  /** 待 Monaco 打开的文件（File → Open File 触发后填入） */
  pendingFileOpen: { path: string; content: string } | null;
  /** 已在编辑器中打开的文件 Tab 列表 */
  openFiles: CodeFile[];
  /** 当前激活的文件 path（null = 无打开文件） */
  activeFilePath: string | null;
  /** 编辑器跳转目标（跨文件 jump / 指定行打开） */
  revealTarget: { path: string; line: number } | null;
  /** 目录条目缓存：path → entries（用于侧栏 FileTreeView） */
  dirEntries: Record<string, Array<{ name: string; path: string; is_dir: boolean }>>;
  /** 目录展开状态：path → bool */
  expandedDirs: Record<string, boolean>;
  /** 当前加载中的目录（用于 spinner） */
  loadingDirs: Record<string, boolean>;

  // Actions
  setSearch: (q: string) => void;
  setFilterKind: (k: SymbolKind | 'all') => void;
  setFilterLanguage: (l: Language | 'all') => void;
  selectSymbol: (id: string | null) => void;
  requestAiExplain: (symbolId: string) => Promise<void>;
  /** V1：CodeNavExtension 直接调，按上下文（symbol 文本）解释 */
  requestAiExplainByContext: (ctx: {
    symbol: string;
    current_file: string;
    line: number;
    selection_start_line?: number | null;
    selection_end_line?: number | null;
    selection_text?: string | null;
    selection_label?: string | null;
  }) => Promise<void>;
  clearAiExplanation: () => void;
  /** 模拟重新索引 */
  reindex: () => void;
  /** V1：调后端 search 替换 mock symbols（失败保留 mock） */
  fetchSymbolsFromBackend: (name: string) => Promise<void>;
  /** V1：调后端 index 并更新状态 */
  triggerBackendIndex: () => Promise<void>;
  /** V1：CodeNavExtension 跨文件跳转请求 */
  openFileAtLine: (filePath: string, line: number) => void;
  clearPendingJump: () => void;
  /** V1：AI 推断置信度过低时记录建议 */
  recordAiSuggestion: (s: { symbol: string; file_path: string; line: number; confidence: number; note: string | null }) => void;
  /** V2：CodeNavExtension 导入文件成功反馈 */
  recordImport: (s: { filePath: string; totalSymbols: number }) => void;
  /** V2：CodeNavExtension 导入文件失败反馈 */
  recordImportError: (filePath: string, error: string) => void;
  /** V2：File → Open Folder 打开的项目根目录（去重） */
  addOpenedProject: (folder: string) => void;
  /** V3：移除一个项目（同步后端） */
  removeOpenedProject: (folder: string) => void;
  /** V3：从后端拉取真实列表（启动时调用） */
  loadOpenedProjects: () => Promise<void>;
  /** V2：File → Open File 待 Monaco 渲染 */
  openFileInEditor: (f: { path: string; content: string }) => void;
  /** V2：消费 pendingFileOpen（layout 调） */
  consumePendingFileOpen: () => { path: string; content: string } | null;
  /** 直接把文件加入编辑器 Tab 列表并激活（去重，内容更新覆盖） */
  openCodeFile: (path: string, content: string) => void;
  /** 关闭一个编辑器 Tab（自动切换激活到邻近 Tab） */
  closeCodeFile: (path: string) => void;
  /** 关闭除指定文件外的所有 Tab */
  closeOtherFiles: (keep: string) => void;
  /** 关闭全部 Tab */
  closeAllFiles: () => void;
  /** 切换激活的 Tab */
  setActiveFile: (path: string) => void;
  /** 设置跳转目标（编辑器消费后 reveal + flash） */
  setRevealTarget: (path: string, line: number) => void;
  /** 清除跳转目标 */
  clearRevealTarget: () => void;
  /** Phase 2F V3：展开/折叠目录（按需调 listDirEntries） */
  toggleDir: (path: string) => Promise<void>;
  /** Phase 2F V3：关闭（移除）已打开的项目 + 同步后端 */
  closeOpenedProject: (folder: string) => void;
  /** Phase 12 V1：附加 Monaco 选区到主对话（用户在编辑器右键「📋 附加到对话」） */
  attachChatSelection: (sel: {
    file: string;
    startLine: number;
    endLine: number;
    text: string;
    label: string;
    auto?: boolean;
  }) => void;
  clearChatSelection: () => void;
}

export const useCodeNavStore = create<CodeNavState>((set, get) => ({
  symbols: [],
  indexStatus: {
    total_files: 0,
    total_symbols: 0,
    last_full_scan: null,
    last_incremental: null,
    is_scanning: false,
  },
  search: '',
  filterKind: 'all',
  filterLanguage: 'all',
  selectedSymbolId: null,
  aiExplanation: null,
  isExplaining: false,
  backendError: null,
  pendingJump: null,
  lastImport: null,
  lastImportError: null,
  openedProjects: [],
  pendingFileOpen: null,
  dirEntries: {},
  expandedDirs: {},
  loadingDirs: {},
  openFiles: [],
  activeFilePath: null,
  revealTarget: null,
  chatSelection: null,

  setSearch: (q) => set({ search: q }),
  setFilterKind: (k) => set({ filterKind: k }),
  setFilterLanguage: (l) => set({ filterLanguage: l }),
  selectSymbol: (id) => set({ selectedSymbolId: id }),
  clearAiExplanation: () => set({ aiExplanation: null, isExplaining: false }),

  requestAiExplain: async (symbolId) => {
    const sym = get().symbols.find((s) => s.id === symbolId);
    if (!sym) return;
    set({ isExplaining: true, aiExplanation: null });
    const t0 = Date.now();
    // Phase 12 V1：先推控制台「running」一行 → 返回 id，结果回来后 updateConsole 原地更新
    const { useTraceStore } = await import('@/store/traceStore');
    const consoleId = useTraceStore.getState().pushConsole({
      category: 'codenav.explain',
      symbol: sym.name,
      text: `▶ 解释中…`,
      source: 'log',
      status: 'running',
    });
    try {
      // 真实后端调用（POST /codenav/explain）—— LLM 真返回语义解释
      const { ipc } = await import('@/ipc/invoke');
      const result = await ipc.codeNavExplain({
        symbol: sym.name,
        current_file: sym.file_path,
        line: sym.start_line,
        context: sym.snippet ?? '',
      });
      const latency = Date.now() - t0;
      set({
        isExplaining: false,
        aiExplanation: {
          symbol_id: symbolId,
          text: result.text || `（${sym.name}）暂无解释`,
          confidence: result.confidence ?? 0.5,
          latency_ms: latency,
          created_at: Date.now(),
        },
      });
      // 原地更新 running 那条（视觉上从 ⏳ ▶ 变 ✓ 文本 + 耗时）
      useTraceStore.getState().updateConsole(consoleId, {
        text: result.source === 'mock' ? `（mock）${result.text}` : result.text,
        fullText: result.text,
        source: result.source,
        status: 'ok',
        latencyMs: latency,
        confidence: result.confidence ?? 0.5,
        backend: result.backend ?? null,
      });
    } catch (e) {
      const latency = Date.now() - t0;
      // 后端挂了 → 用模板兜底（避免空白面板）
      const templates = AI_EXPLANATION_TEMPLATES[sym.kind] ?? ['该符号无解释模板'];
      const tmpl = templates[Math.floor(Math.random() * templates.length)];
      const text = tmpl
        .replace('{name}', sym.name)
        .replace('{parent}', sym.parent_class ?? 'Unknown')
        .replace('{n}', String(sym.end_line - sym.start_line + 1))
        .replace('{deps}', 'react / zustand / @tauri-apps/api');
      set({
        isExplaining: false,
        aiExplanation: {
          symbol_id: symbolId,
          text,
          confidence: 0.3,
          latency_ms: latency,
          created_at: Date.now(),
        },
      });
      useTraceStore.getState().updateConsole(consoleId, {
        text: `✗ 后端失败，已用模板兜底：${text}`,
        source: 'mock',
        status: 'err',
        latencyMs: latency,
      });
      // eslint-disable-next-line no-console
      console.warn('[requestAiExplain] 后端调用失败，已用模板兜底:', e);
    }
  },

  requestAiExplainByContext: async (ctx) => {
    set({ isExplaining: true, aiExplanation: null });
    // Phase 12 V1：先把「running」推入控制台 —— 返回 id，结果回来后 updateConsole 原地更新
    const { useTraceStore } = await import('@/store/traceStore');
    const t0 = Date.now();
    const consoleText = ctx.selection_label
      ? `▶ 解释中… · ${ctx.selection_label}`
      : `▶ 解释中…`;
    const consoleId = useTraceStore.getState().pushConsole({
      category: 'codenav.explain',
      symbol: ctx.symbol,
      text: consoleText,
      source: 'log',
      status: 'running',
    });
    try {
      const { ipc } = await import('@/ipc/invoke');
      const context = await readCurrentFileContext(ctx.current_file);
      const result = await ipc.codeNavExplain({
        symbol: ctx.symbol,
        current_file: ctx.current_file,
        line: ctx.line,
        context,
        selection_start_line: ctx.selection_start_line ?? null,
        selection_end_line: ctx.selection_end_line ?? null,
        selection_text: ctx.selection_text ?? null,
      });
      const latency = Date.now() - t0;
      set({
        isExplaining: false,
        aiExplanation: {
          symbol_id: `ctx-${ctx.symbol}-${ctx.line}`,
          text: result.text,
          confidence: result.confidence,
          latency_ms: latency,
          created_at: Date.now(),
        },
      });
      // 原地更新 running 那条 —— 视觉上从 ⏳ ▶ 变 ✓ 文本 + 耗时（流式体验）
      const okText = ctx.selection_label
        ? `✓ ${ctx.symbol}（${ctx.selection_label}）`
        : `✓ ${ctx.symbol}`;
      useTraceStore.getState().updateConsole(consoleId, {
        text: result.source === 'mock' ? `（mock）${result.text}` : okText,
        fullText: result.text,
        source: result.source,
        status: 'ok',
        latencyMs: latency,
        confidence: result.confidence,
        backend: result.backend ?? null,
      });
    } catch (e) {
      const latency = Date.now() - t0;
      set({
        isExplaining: false,
        aiExplanation: {
          symbol_id: `ctx-${ctx.symbol}-${ctx.line}`,
          text: `❌ AI 解释失败：${String(e)}`,
          confidence: 0,
          latency_ms: latency,
          created_at: Date.now(),
        },
      });
      useTraceStore.getState().updateConsole(consoleId, {
        category: 'codenav.explain',
        symbol: ctx.symbol,
        text: `✗ 解释失败 · ${String(e)}`,
        source: 'log',
        status: 'err',
        latencyMs: latency,
      });
    }
  },

  reindex: () => {
    set({
      indexStatus: {
        total_files: 0,
        total_symbols: 0,
        last_full_scan: Date.now(),
        last_incremental: null,
        is_scanning: true,
      },
    });
    // 模拟 1.5s "扫描"后回落
    setTimeout(() => {
      set((s) => ({
        indexStatus: { ...s.indexStatus, is_scanning: false, last_incremental: Date.now() },
      }));
    }, 1500);
  },

  // -------------------------------------------------------------------------
  // V1: 接后端的 action（V0 不调，保留 mock 离线体验）
  // -------------------------------------------------------------------------

  fetchSymbolsFromBackend: async (name) => {
    try {
      const { ipc } = await import('@/ipc/invoke');
      const list = await ipc.codeNavListSymbols(name, undefined, 50);
      // 映射后端字段 → 前端 Symbol
      const mapped: Symbol[] = list.map((r, i) => ({
        id: `be-${i}-${r.name}-${r.start_line}`,
        name: r.name,
        kind: r.kind as Symbol['kind'],
        file_path: r.file_path,
        start_line: r.start_line,
        end_line: r.end_line,
        signature: r.signature,
        parent_class: r.parent_class,
        language: r.language as Language,
        last_modified: Date.now(),
        snippet: '',
      }));
      if (mapped.length > 0) {
        set({ symbols: mapped, backendError: null });
      }
      // 后端返回空：保留 mock，不报错
    } catch (e) {
      set({ backendError: String(e) });
    }
  },

  triggerBackendIndex: async () => {
    set((s) => ({ indexStatus: { ...s.indexStatus, is_scanning: true } }));
    try {
      const { ipc } = await import('@/ipc/invoke');
      const status = await ipc.codeNavIndex();
      set({ indexStatus: status, backendError: null });
    } catch (e) {
      set({
        indexStatus: {
          total_files: get().indexStatus.total_files,
          total_symbols: get().indexStatus.total_symbols,
          last_full_scan: null,
          last_incremental: null,
          is_scanning: false,
        },
        backendError: String(e),
      });
    }
  },

  openFileAtLine: (filePath, line) => {
    set({
      pendingJump: { file_path: filePath, line },
      revealTarget: { path: filePath, line },
    });
  },

  clearPendingJump: () => set({ pendingJump: null }),

  recordAiSuggestion: (s) => {
    // V1 简化为 aiExplanation 占位；V2 接 AI 解释面板组件
    set({
      aiExplanation: {
        symbol_id: `ai-${Date.now()}`,
        text: `🤖 AI 推断：符号 ${s.symbol} → ${s.file_path}:${s.line}（置信度 ${s.confidence.toFixed(2)}）${s.note ? ' · ' + s.note : ''}`,
        confidence: s.confidence,
        latency_ms: 0,
        created_at: Date.now(),
      },
    });
  },

  recordImport: (s) => {
    set({
      lastImport: { filePath: s.filePath, totalSymbols: s.totalSymbols, at: Date.now() },
      lastImportError: null,
    });
  },

  recordImportError: (filePath, error) => {
    set({
      lastImport: null,
      lastImportError: { filePath, error, at: Date.now() },
    });
  },

  addOpenedProject: (folder) => {
    set((s) => {
      if (s.openedProjects.includes(folder)) return s;
      const next = [...s.openedProjects, folder];
      // 异步同步到后端
      void import('@/ipc/invoke').then(({ ipc }) =>
        ipc.codeNavSyncOpenedProjects(next).catch(() => {}),
      );
      return { openedProjects: next };
    });
  },

  removeOpenedProject: (folder) => {
    set((s) => {
      const next = s.openedProjects.filter((p) => p !== folder);
      if (next.length === s.openedProjects.length) return s;
      void import('@/ipc/invoke').then(({ ipc }) =>
        ipc.codeNavSyncOpenedProjects(next).catch(() => {}),
      );
      return { openedProjects: next };
    });
  },

  loadOpenedProjects: async () => {
    try {
      const { ipc } = await import('@/ipc/invoke');
      const r = await ipc.codeNavOpenedProjects();
      set({ openedProjects: r.opened_projects ?? [] });
    } catch {
      // 启动时后端可能还没就绪 — 静默忽略
    }
  },

  openFileInEditor: (f) => {
    // 同时维护 pendingFileOpen（向后兼容）与 openFiles（实际渲染源）
    set({ pendingFileOpen: { path: f.path, content: f.content } });
    get().openCodeFile(f.path, f.content);
  },

  consumePendingFileOpen: () => {
    const cur = get().pendingFileOpen;
    if (cur) set({ pendingFileOpen: null });
    return cur;
  },

  openCodeFile: (path, content) => {
    set((s) => {
      const exists = s.openFiles.some((f) => f.path === path);
      if (exists) {
        return {
          openFiles: s.openFiles.map((f) => (f.path === path ? { ...f, content } : f)),
          activeFilePath: path,
        };
      }
      return {
        openFiles: [...s.openFiles, { path, content, language: detectLanguage(path) }],
        activeFilePath: path,
      };
    });
  },

  closeCodeFile: (path) => {
    set((s) => {
      const idx = s.openFiles.findIndex((f) => f.path === path);
      if (idx < 0) return {};
      const next = s.openFiles.filter((f) => f.path !== path);
      let active = s.activeFilePath;
      if (active === path) {
        active = next.length ? next[Math.max(0, idx - 1)].path : null;
      }
      return { openFiles: next, activeFilePath: active };
    });
  },

  closeOtherFiles: (keep) => {
    set((s) => {
      const kept = s.openFiles.find((f) => f.path === keep);
      if (!kept) return {};
      return { openFiles: [kept], activeFilePath: keep };
    });
  },

  closeAllFiles: () => set({ openFiles: [], activeFilePath: null }),

  setActiveFile: (path) => set({ activeFilePath: path }),

  setRevealTarget: (path, line) => set({ revealTarget: { path, line } }),

  clearRevealTarget: () => set({ revealTarget: null }),

  // -- Phase 2F V3：侧栏文件树 --

  toggleDir: async (path) => {
    const cur = get().expandedDirs[path];
    if (cur) {
      // 已展开 → 折叠
      set((s) => {
        const next = { ...s.expandedDirs };
        delete next[path];
        return { expandedDirs: next };
      });
      return;
    }
    // 未展开 → 标记展开 + 按需加载
    set((s) => ({
      expandedDirs: { ...s.expandedDirs, [path]: true },
      loadingDirs: { ...s.loadingDirs, [path]: true },
    }));
    try {
      const { ipc } = await import('@/ipc/invoke');
      const entries = await ipc.listDirEntries(path);
      set((s) => ({
        dirEntries: { ...s.dirEntries, [path]: entries },
        loadingDirs: { ...s.loadingDirs, [path]: false },
      }));
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[toggleDir] listDirEntries failed:', e);
      set((s) => ({ loadingDirs: { ...s.loadingDirs, [path]: false } }));
    }
  },

  closeOpenedProject: (folder) => {
    set((s) => {
      const nextProjects = s.openedProjects.filter((p) => p !== folder);
      // 关闭该项目下所有展开状态 + 条目缓存
      const prefix = folder.replace(/\\/g, '/');
      const nextExpanded: Record<string, boolean> = {};
      const nextEntries: Record<string, Array<{ name: string; path: string; is_dir: boolean }>> = {};
      Object.keys(s.expandedDirs).forEach((k) => {
        const nk = k.replace(/\\/g, '/');
        if (!nk.startsWith(prefix + '/') && nk !== prefix) nextExpanded[k] = s.expandedDirs[k];
      });
      Object.keys(s.dirEntries).forEach((k) => {
        const nk = k.replace(/\\/g, '/');
        if (!nk.startsWith(prefix + '/') && nk !== prefix) nextEntries[k] = s.dirEntries[k];
      });
      // 同步到后端
      void import('@/ipc/invoke').then(({ ipc }) =>
        ipc.codeNavSyncOpenedProjects(nextProjects).catch(() => {}),
      );
      return {
        openedProjects: nextProjects,
        expandedDirs: nextExpanded,
        dirEntries: nextEntries,
      };
    });
  },

  // Phase 12 V1：附加 Monaco 选区到主对话（ChatInput 显示 chip + 发送时拼到 user message）
  attachChatSelection: (sel) => set({ chatSelection: sel }),
  clearChatSelection: () => set({ chatSelection: null }),
}));

// ---------- 派生选择器 ----------

export const selectFilteredSymbols = (s: CodeNavState): Symbol[] => {
  const q = s.search.toLowerCase().trim();
  return s.symbols
    .filter((sym) => (s.filterKind === 'all' ? true : sym.kind === s.filterKind))
    .filter((sym) => (s.filterLanguage === 'all' ? true : sym.language === s.filterLanguage))
    .filter((sym) => {
      if (!q) return true;
      return (
        sym.name.toLowerCase().includes(q) ||
        sym.file_path.toLowerCase().includes(q) ||
        (sym.signature ?? '').toLowerCase().includes(q) ||
        (sym.parent_class ?? '').toLowerCase().includes(q)
      );
    })
    .sort((a, b) => {
      // kind 优先级：class > method > function > field > interface > enum
      const order: Record<SymbolKind, number> = {
        class: 0, method: 1, function: 2, field: 3, interface: 4, enum: 5,
      };
      if (order[a.kind] !== order[b.kind]) return order[a.kind] - order[b.kind];
      return a.start_line - b.start_line;
    });
};

export const selectSelectedSymbol = (s: CodeNavState): Symbol | null => {
  if (!s.selectedSymbolId) return null;
  return s.symbols.find((sym) => sym.id === s.selectedSymbolId) ?? null;
};
