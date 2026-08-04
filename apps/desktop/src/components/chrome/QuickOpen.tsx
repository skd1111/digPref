/**
 * QuickOpen —— VSCode Ctrl+P 风格的快速打开。
 *
 * 显示系统资产列表 + 简单模糊匹配。回车执行"打开"动作（占位：pin 到 chat）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useUIStore } from '@/store/uiStore';
import { useAssetStore, type AssetNode } from '@/store/assetStore';

const TYPE_ICON: Record<AssetNode['type'], string> = {
  database: '🗄',
  rest: '🌐',
  ssh: '🔐',
  rpa: '🤖',
};

export function QuickOpen(): JSX.Element | null {
  const open = useUIStore((s) => s.quickOpenOpen);
  const toggle = useUIStore((s) => s.toggleQuickOpen);
  const tree = useAssetStore((s) => s.tree);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open) {
      setQuery('');
      setHighlight(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  const items = useMemo<AssetNode[]>(() => {
    const list = tree;
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (n) => n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q),
    );
  }, [tree, query]);

  if (!open) return null;

  const open_ = (n: AssetNode): void => {
    toggle(false);
    setTimeout(() => alert(`打开 "${n.label}" —— 占位：未来会 pin 到 chat 或在编辑器打开`), 0);
  };

  return (
    <div
      className="fixed inset-0 z-[140] flex items-start justify-center pt-[80px]"
      style={{ backgroundColor: 'rgba(0,0,0,0.4)' }}
      onClick={() => toggle(false)}
    >
      <div
        className="w-[520px] overflow-hidden rounded shadow-2xl"
        style={{ backgroundColor: '#f3f3f3', border: '1px solid #d0d0d0' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center border-b px-3"
          style={{ borderColor: '#e0e0e0' }}
        >
          <span className="text-fg-muted">›</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setHighlight(0);
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setHighlight((h) => Math.min(h + 1, items.length - 1));
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setHighlight((h) => Math.max(h - 1, 0));
              } else if (e.key === 'Enter') {
                e.preventDefault();
                const it = items[highlight];
                if (it) open_(it);
              }
            }}
            placeholder="输入名称快速打开（资产 / 后续支持文件）"
            className="flex-1 bg-transparent py-3 pl-2 text-ui outline-none placeholder:text-fg-muted"
            style={{ color: '#1f1f1f' }}
          />
        </div>

        <ul
          className="max-h-[360px] overflow-auto py-1"
          onMouseLeave={() => setHighlight(-1)}
        >
          {items.length === 0 ? (
            <li className="px-3 py-2 text-fg-muted">无匹配</li>
          ) : (
            items.map((n, i) => (
              <li
                key={n.id}
                onMouseEnter={() => setHighlight(i)}
                onClick={() => open_(n)}
                className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-ui"
                style={{
                  backgroundColor: i === highlight ? '#04395e' : 'transparent',
                  color: '#1f1f1f',
                }}
              >
                <span className="text-fg-muted">{TYPE_ICON[n.type]}</span>
                <span className="flex-1">{n.label}</span>
                <span className="text-2xs text-fg-muted">{n.type}</span>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
