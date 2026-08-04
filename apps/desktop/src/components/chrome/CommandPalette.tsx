/**
 * CommandPalette — VSCode 风格命令面板。
 *
 * 按 Ctrl+Shift+P 触发：
 *   - 顶部输入框，键入即过滤命令
 *   - 下方列表：每条命令 = (title, description/category)
 *   - ↑/↓ 切换高亮，Enter 执行，Esc 关闭
 *   - 鼠标点击也可以执行
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useUIStore } from '@/store/uiStore';
import { getAllCommands, runCommand, type Command } from '@/commands/commandRegistry';

export function CommandPalette(): JSX.Element | null {
  const open = useUIStore((s) => s.commandPaletteOpen);
  const toggle = useUIStore((s) => s.toggleCommandPalette);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // 打开时聚焦 + 重置
  useEffect(() => {
    if (open) {
      setQuery('');
      setHighlight(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  // 关闭时拦截事件
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault();
        toggle(false);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open, toggle]);

  const allCommands = useMemo(() => (open ? getAllCommands() : []), [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allCommands;
    return allCommands.filter(
      (c) =>
        c.id.toLowerCase().includes(q) ||
        c.title.toLowerCase().includes(q) ||
        (c.category ?? '').toLowerCase().includes(q) ||
        (c.description ?? '').toLowerCase().includes(q),
    );
  }, [allCommands, query]);

  if (!open) return null;

  const execute = (cmd: Command): void => {
    toggle(false);
    setTimeout(() => runCommand(cmd.id), 0);
  };

  return (
    <div
      className="fixed inset-0 z-[150] flex items-start justify-center pt-[80px]"
      style={{ backgroundColor: 'rgba(0,0,0,0.4)' }}
      onClick={() => toggle(false)}
    >
      <div
        className="w-[600px] overflow-hidden rounded shadow-2xl"
        style={{
          backgroundColor: '#f3f3f3',
          border: '1px solid #d0d0d0',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 输入框 */}
        <div
          className="flex items-center border-b px-3"
          style={{ borderColor: '#e0e0e0' }}
        >
          <span className="text-fg-muted">⌕</span>
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
                setHighlight((h) => Math.min(h + 1, filtered.length - 1));
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setHighlight((h) => Math.max(h - 1, 0));
              } else if (e.key === 'Enter') {
                e.preventDefault();
                const c = filtered[highlight];
                if (c) execute(c);
              }
            }}
            placeholder="输入命令名称..."
            className="flex-1 bg-transparent py-3 pl-2 text-ui outline-none placeholder:text-fg-muted"
            style={{ color: '#1f1f1f' }}
          />
        </div>

        {/* 列表 */}
        <ul
          className="max-h-[400px] overflow-auto py-1"
          onMouseLeave={() => setHighlight(-1)}
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-fg-muted">没有匹配的命令</li>
          ) : (
            filtered.map((c, i) => (
              <li
                key={c.id}
                onMouseEnter={() => setHighlight(i)}
                onClick={() => execute(c)}
                className="flex cursor-pointer items-center justify-between px-3 py-1.5 text-ui"
                style={{
                  backgroundColor: i === highlight ? '#04395e' : 'transparent',
                  color: '#1f1f1f',
                }}
              >
                <div className="flex-1">
                  <div>{c.title}</div>
                  {c.description && (
                    <div className="text-2xs text-fg-muted">{c.description}</div>
                  )}
                </div>
                {c.category && (
                  <span
                    className="ml-2 rounded px-1.5 py-0.5 text-2xs"
                    style={{
                      backgroundColor: '#ffffff',
                      color: '#616161',
                    }}
                  >
                    {c.category}
                  </span>
                )}
              </li>
            ))
          )}
        </ul>

        <div
          className="border-t px-3 py-1.5 text-2xs"
          style={{ borderColor: '#e0e0e0', color: '#616161' }}
        >
          ↑↓ 选择 · Enter 执行 · Esc 关闭
        </div>
      </div>
    </div>
  );
}
