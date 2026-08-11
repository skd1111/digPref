/**
 * FindInFiles —— VSCode Ctrl+Shift+F 风格的搜索面板。
 *
 * 在 ActivityBar 切到 search 视图时显示。
 * 当前实现：在系统资产上做简单的子串匹配。
 *
 * Phase 2F 改造：顶部加 2-tab toggle「资产 / 代码符号」，点击切换：
 *   - 资产（默认）：原 FindInFiles 行为不变
 *   - 代码符号：CodeNavSearch + SymbolDetail 双栏布局
 */
import { useMemo, useState } from 'react';
import { useAssetStore, type AssetNode } from '@/store/assetStore';
import { CodeNavSearch } from '@/components/codenav/CodeNavSearch';
import { SymbolDetail } from '@/components/codenav/SymbolDetail';

type SearchMode = 'asset' | 'symbol';

const TYPE_ICON: Record<AssetNode['type'], string> = {
  database: '🗄',
  rest: '🌐',
  ssh: '🔐',
  rpa: '🤖',
};

export function FindInFiles({ defaultMode }: { defaultMode?: SearchMode } = {}): JSX.Element {
  const tree = useAssetStore((s) => s.tree);
  const [query, setQuery] = useState('');
  const [regex, setRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);
  // Phase 2F：顶部 2-tab 切换
  // Phase 2F V0 收尾 (2026-07-28)：code-nav 顶级入口传 defaultMode='symbol' 直接进 symbol 模式
  const [mode, setMode] = useState<SearchMode>(defaultMode ?? 'asset');

  // ---- 资产搜索结果（mode='symbol' 时不使用，但 useMemo 必须无条件调用，
  //      否则切换 mode 会改变 Hook 调用顺序，触发 React #300）----
  const items = useMemo(() => {
    const list = tree;
    if (!query) return [];
    const hits: { node: AssetNode; matchedField: string }[] = [];
    for (const n of list) {
      const fieldsToCheck = [n.label, n.id, JSON.stringify(n.meta)];
      let matcher: (s: string) => boolean;
      if (regex) {
        try {
          const re = new RegExp(query, caseSensitive ? '' : 'i');
          matcher = (s) => re.test(s);
        } catch {
          matcher = () => false;
        }
      } else if (wholeWord) {
        const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const re = new RegExp(`\\b${escaped}\\b`, caseSensitive ? '' : 'i');
        matcher = (s) => re.test(s);
      } else {
        const q = caseSensitive ? query : query.toLowerCase();
        matcher = (s) => (caseSensitive ? s : s.toLowerCase()).includes(q);
      }
      for (const f of fieldsToCheck) {
        if (matcher(f)) {
          hits.push({ node: n, matchedField: f.slice(0, 60) });
          break;
        }
      }
    }
    return hits;
  }, [tree, query, regex, caseSensitive, wholeWord]);

  // ---- 所有 Hook 已在上方无条件调用完毕，此处再 early-return 安全 ----
  if (mode === 'symbol') {
    return (
      <div className="flex h-full flex-col">
        <ModeTabs mode={mode} onChange={setMode} />
        <div className="flex flex-1 overflow-hidden">
          <div
            className="flex-shrink-0 overflow-hidden"
            style={{ width: 320, borderRight: '1px solid #e0e0e0' }}
          >
            <CodeNavSearch />
          </div>
          <div className="flex-1 overflow-hidden">
            <SymbolDetail />
          </div>
        </div>
      </div>
    );
  }

  // ---- mode = asset（原 FindInFiles 行为）----
  return (
    <div className="flex h-full flex-col">
      <ModeTabs mode={mode} onChange={setMode} />
      {/* 输入区 */}
      <div
        className="space-y-1 border-b p-2"
        style={{ borderColor: '#e0e0e0' }}
      >
        <div
          className="flex items-center rounded px-2 py-1"
          style={{ backgroundColor: '#ececec', border: '1px solid #d4d4d4' }}
        >
          <span className="text-fg-muted">⌕</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="跨系统资产搜索…"
            className="ml-2 flex-1 bg-transparent text-ui outline-none placeholder:text-fg-muted"
            style={{ color: '#1f1f1f' }}
          />
        </div>
        <div className="flex items-center gap-2 text-2xs text-fg-muted">
          <ToggleBtn on={regex} onClick={() => setRegex((v) => !v)} label=".*" title="正则" />
          <ToggleBtn on={caseSensitive} onClick={() => setCaseSensitive((v) => !v)} label="Aa" title="大小写" />
          <ToggleBtn on={wholeWord} onClick={() => setWholeWord((v) => !v)} label="ab" title="整词" />
        </div>
      </div>

      {/* 结果区 */}
      <div className="flex-1 overflow-auto p-2 text-ui">
        {!query ? (
          <div className="text-2xs text-fg-muted">输入关键字开始搜索</div>
        ) : items.length === 0 ? (
          <div className="text-2xs text-fg-muted">没有匹配的结果</div>
        ) : (
          <ul>
            {items.map((hit) => (
              <li
                key={hit.node.id}
                className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-vscode-border"
              >
                <span className="text-fg-muted">{TYPE_ICON[hit.node.type]}</span>
                <span className="truncate">{hit.node.label}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div
        className="border-t px-2 py-1 text-2xs"
        style={{ borderColor: '#e0e0e0', color: '#616161' }}
      >
        {items.length} 个结果
      </div>
    </div>
  );
}

function ToggleBtn({
  on,
  onClick,
  label,
  title,
}: {
  on: boolean;
  onClick: () => void;
  label: string;
  title: string;
}): JSX.Element {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="rounded px-1.5"
      style={{
        backgroundColor: on ? '#007acc' : 'transparent',
        color: on ? '#ffffff' : '#616161',
        border: '1px solid #d4d4d4',
      }}
    >
      {label}
    </button>
  );
}

/**
 * ModeTabs —— Phase 2F 顶部 2-tab 切换：资产 vs 代码符号。
 */
function ModeTabs({
  mode,
  onChange,
}: {
  mode: SearchMode;
  onChange: (m: SearchMode) => void;
}): JSX.Element {
  return (
    <div
      className="flex h-[32px] flex-shrink-0 items-stretch"
      style={{ borderBottom: '1px solid #e0e0e0' }}
    >
      {(
        [
          { key: 'asset', label: '资产', icon: '🗄' },
          { key: 'symbol', label: '代码符号', icon: '⌘' },
        ] as Array<{ key: SearchMode; label: string; icon: string }>
      ).map((t) => {
        const active = t.key === mode;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            className="flex-1 text-2xs transition-colors"
            style={{
              backgroundColor: active ? '#ffffff' : 'transparent',
              color: active ? '#0451a5' : '#616161',
              borderBottom: active ? '2px solid #007acc' : '2px solid transparent',
              fontWeight: active ? 600 : 400,
            }}
          >
            <span className="mr-1">{t.icon}</span>
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
