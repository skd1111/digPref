/**
 * CodeNavSearch —— Phase 2F 代码符号搜索列表（V0 MVP）。
 *
 * 布局：
 *   ┌─ 搜索框 ──────────────┐
 *   │ kind filter (chips)   │
 *   │ language filter (chips)│
 *   ├────────────────────────┤
 *   │ 结果列表：              │
 *   │   [类] OrderService     │
 *   │   com/eaide/order/...   │
 *   │   L12-L87  ·  Java      │
 *   └────────────────────────┘
 *
 * 选中态视觉：仿 auditStore ApprovalQueue 的 `#094771` 背景 + `#007acc` 3px border。
 */
import { useEffect } from 'react';
import {
  selectFilteredSymbols,
  useCodeNavStore,
} from '@/store/codeNavStore';
import {
  KIND_COLORS,
  LANGUAGE_COLORS,
  type Language,
  type Symbol,
  type SymbolKind,
} from '@/types/codenav';

const KINDS: Array<SymbolKind | 'all'> = [
  'all', 'class', 'method', 'function', 'interface', 'field', 'enum',
];

const LANGUAGES: Array<Language | 'all'> = ['all', ...(Object.keys(LANGUAGE_COLORS) as Language[])];

function SymbolRow({
  sym,
  selected,
  onClick,
}: {
  sym: Symbol;
  selected: boolean;
  onClick: () => void;
}): JSX.Element {
  const kind = KIND_COLORS[sym.kind];
  const lang = LANGUAGE_COLORS[sym.language];
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full border-b px-3 py-2.5 text-left transition-colors"
      style={{
        borderColor: '#e0e0e0',
        backgroundColor: selected ? '#0e639c' : 'transparent',
        borderLeft: selected ? '3px solid #007acc' : '3px solid transparent',
      }}
    >
      <div className="flex items-start gap-2">
        <span
          className="flex-shrink-0 rounded px-1.5 py-0.5 text-2xs font-semibold"
          style={{ backgroundColor: kind.bg, color: kind.fg }}
        >
          {kind.label}
        </span>
        <div className="min-w-0 flex-1">
          <div
            className="truncate font-mono text-ui font-semibold"
            style={{ color: '#1f1f1f' }}
            title={sym.name}
          >
            {sym.name}
            {sym.parent_class && (
              <span className="ml-1 text-2xs font-normal" style={{ color: '#616161' }}>
                · {sym.parent_class}
              </span>
            )}
          </div>
          <div
            className="mt-0.5 truncate text-2xs"
            style={{ color: '#616161' }}
            title={sym.file_path}
          >
            {sym.file_path}
          </div>
          <div className="mt-1 flex items-center gap-1.5 text-2xs" style={{ color: '#616161' }}>
            <span>
              L{sym.start_line}–L{sym.end_line}
            </span>
            <span>·</span>
            <span
              className="rounded px-1 py-0.5"
              style={{ backgroundColor: lang.bg, color: lang.fg, fontWeight: 600 }}
            >
              {lang.label}
            </span>
            {sym.signature && (
              <>
                <span>·</span>
                <span
                  className="truncate font-mono"
                  style={{ color: '#0b6bcb' }}
                  title={sym.signature}
                >
                  {sym.signature}
                </span>
              </>
            )}
          </div>
        </div>
      </div>
    </button>
  );
}

export function CodeNavSearch(): JSX.Element {
  const search = useCodeNavStore((s) => s.search);
  const setSearch = useCodeNavStore((s) => s.setSearch);
  const filterKind = useCodeNavStore((s) => s.filterKind);
  const setFilterKind = useCodeNavStore((s) => s.setFilterKind);
  const filterLanguage = useCodeNavStore((s) => s.filterLanguage);
  const setFilterLanguage = useCodeNavStore((s) => s.setFilterLanguage);
  const selectedId = useCodeNavStore((s) => s.selectedSymbolId);
  const selectSymbol = useCodeNavStore((s) => s.selectSymbol);
  const reindex = useCodeNavStore((s) => s.reindex);
  const indexStatus = useCodeNavStore((s) => s.indexStatus);

  // 挂载时拉后端真实符号（修复 mock 永远不变的坑 —— File → Open Folder 后列表不变）
  useEffect(() => {
    void (async () => {
      const { ipc } = await import('@/ipc/invoke');
      // 1) 等 Agent 就绪（最多 10s）
      const ready = await ipc.agentWaitReady(10);
      if (!ready.ready) return;
      // 2) 触发后端索引（已有项目时跑增量；没项目时无害）
      try {
        await ipc.codeNavIndex({});
      } catch {
        // ignore
      }
      // 3) 拉符号列表
      try {
        const r = await ipc.codeNavListSymbols('', undefined, 500);
        // 后端返回 list[dict] 直接 —— 也兼容 {symbols: [...]} 包装（前端 IPC 习惯）
        const symbols = (Array.isArray(r) ? r : (r as any).symbols ?? []) as Array<{
          name: string;
          kind: string;
          file_path: string;
          start_line: number;
          end_line: number;
          signature?: string;
          parent_class?: string | null;
          language: string;
        }>;
        const list = symbols.map((b) => ({
          id: `be-${b.file_path}:${b.start_line}-${b.name}`,
          name: b.name,
          kind: b.kind as SymbolKind,
          file_path: b.file_path,
          start_line: b.start_line,
          end_line: b.end_line,
          signature: b.signature ?? '',
          parent_class: b.parent_class ?? null,
          language: b.language as Language,
          last_modified: Date.now(),
          snippet: '',
        }));
        useCodeNavStore.setState(() => ({
          symbols: list,
          // 单独取一次 status（如果有的话）；这里不强依赖
          indexStatus: {
            total_files: list.length > 0 ? Math.ceil(list.length / 3) : 0,
            total_symbols: list.length,
            last_full_scan: Date.now() / 1000,
            last_incremental: null,
            is_scanning: false,
          },
        }));
      } catch {
        // 后端还没索引完 —— 保留 mock fallback
      }
    })();
  }, []);

  // 直接用 selectFilteredSymbols（标准 zustand selector pattern）。
  // SymbolDetail 已被 GLM fix 覆盖 React #300 真正根因，此处无需特殊处理。
  const list = useCodeNavStore(selectFilteredSymbols);

  return (
    <div className="flex h-full flex-col">
      {/* 头部：搜索 + filter chips */}
      <div
        className="flex-shrink-0 space-y-2 border-b p-3"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
      >
        {/* 搜索框 */}
        <input
          type="text"
          placeholder="搜索符号名 / 文件路径 / 签名..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded border px-2 py-1 text-2xs focus:outline-none"
          style={{
            backgroundColor: '#ffffff',
            borderColor: '#d4d4d4',
            color: '#1f1f1f',
          }}
        />

        {/* Kind filter */}
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-2xs" style={{ color: '#616161' }}>
            Kind:
          </span>
          {KINDS.map((k) => {
            const active = k === filterKind;
            const meta = k === 'all' ? null : KIND_COLORS[k];
            return (
              <button
                key={k}
                type="button"
                onClick={() => setFilterKind(k)}
                className="rounded px-1.5 py-0.5 text-2xs transition-colors"
                style={{
                  backgroundColor: active ? (meta?.bg ?? '#007acc') : 'transparent',
                  color: active ? (meta?.fg ?? '#ffffff') : '#616161',
                  border: `1px solid ${active ? (meta?.bg ?? '#007acc') : '#1f1f1f'}`,
                  fontWeight: active ? 600 : 400,
                }}
              >
                {k === 'all' ? '全部' : meta?.label}
              </button>
            );
          })}
        </div>

        {/* Language filter */}
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-2xs" style={{ color: '#616161' }}>
            Lang:
          </span>
          {LANGUAGES.map((l) => {
            const active = l === filterLanguage;
            const meta = l === 'all' ? null : LANGUAGE_COLORS[l];
            return (
              <button
                key={l}
                type="button"
                onClick={() => setFilterLanguage(l)}
                className="rounded px-1.5 py-0.5 text-2xs transition-colors"
                style={{
                  backgroundColor: active ? (meta?.bg ?? '#007acc') : 'transparent',
                  color: active ? (meta?.fg ?? '#ffffff') : '#616161',
                  border: `1px solid ${active ? (meta?.bg ?? '#007acc') : '#1f1f1f'}`,
                  fontWeight: active ? 600 : 400,
                }}
              >
                {l === 'all' ? '全部' : meta?.label}
              </button>
            );
          })}
        </div>

        {/* 索引状态条 */}
        <div
          className="flex items-center justify-between rounded px-2 py-1 text-2xs"
          style={{ backgroundColor: '#ffffff', color: '#616161' }}
        >
          <span>
            🔍 {list.length} 匹配 / {indexStatus?.total_symbols ?? 0} 符号 / {indexStatus?.total_files ?? 0} 文件
            {indexStatus?.is_scanning && (
              <span className="ml-2" style={{ color: '#795e26' }}>
                ⏳ 扫描中…
              </span>
            )}
          </span>
          <button
            type="button"
            onClick={reindex}
            className="rounded px-1.5 py-0.5 transition-colors hover:bg-vscode-border"
            style={{ color: '#0451a5' }}
            title="从后端重新拉取索引"
          >
            ↻ 重建索引
          </button>
        </div>
      </div>

      {/* 结果列表 */}
      <div className="flex-1 overflow-auto">
        {list.length === 0 ? (
          <div
            className="flex h-full flex-col items-center justify-center p-6 text-center text-2xs"
            style={{ color: '#616161' }}
          >
            <div className="mb-2 text-3xl">⌕</div>
            <div>无匹配符号</div>
            <div className="mt-1">调整搜索词 / filter 后重试</div>
          </div>
        ) : (
          list.map((sym) => (
            <SymbolRow
              key={sym.id}
              sym={sym}
              selected={sym.id === selectedId}
              onClick={() => selectSymbol(sym.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}
