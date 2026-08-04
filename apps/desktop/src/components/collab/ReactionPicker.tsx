/**
 * ReactionPicker —— 5 个 emoji 快捷回应（👍 👎 ✅ ❌ 👀）。
 *
 * 行为：点击 toggle（同 emoji 取消，异 emoji 切换）。
 * 设计：点击空白处收起 popover（设计文档 §6.1）。
 */
import { useEffect, useRef, useState } from 'react';
import { CURRENT_USER, type ReactionEmoji, type ReactionMap } from '@/types/collab';

const EMOJIS: ReactionEmoji[] = ['👍', '👎', '✅', '❌', '👀'];

interface ReactionPickerProps {
  reactions: ReactionMap;
  onToggle: (emoji: ReactionEmoji) => void;
}

export function ReactionPicker({ reactions, onToggle }: ReactionPickerProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent): void => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener('mousedown', onClick);
    return () => window.removeEventListener('mousedown', onClick);
  }, [open]);

  // 聚合：emoji -> 计数 + 是否包含我
  const counts: Record<ReactionEmoji, { count: number; mine: boolean }> = {
    '👍': { count: 0, mine: false },
    '👎': { count: 0, mine: false },
    '✅': { count: 0, mine: false },
    '❌': { count: 0, mine: false },
    '👀': { count: 0, mine: false },
  };
  for (const [uid, emo] of Object.entries(reactions)) {
    if (counts[emo]) {
      counts[emo].count += 1;
      if (uid === CURRENT_USER.id) counts[emo].mine = true;
    }
  }

  return (
    <div className="flex items-center gap-1.5">
      {EMOJIS.map((emo) => {
        const { count, mine } = counts[emo];
        if (count === 0) return null;
        return (
          <button
            key={emo}
            type="button"
            onClick={() => onToggle(emo)}
            className="flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs transition-colors"
            style={{
              backgroundColor: mine ? 'rgba(99, 102, 241, 0.18)' : '#ececec',
              border: `1px solid ${mine ? '#6366f1' : '#4a4a4a'}`,
              color: mine ? '#4f46e5' : '#1f1f1f',
            }}
            title={mine ? '点击取消' : '点击回应'}
          >
            <span>{emo}</span>
            <span style={{ fontVariantNumeric: 'tabular-nums' }}>{count}</span>
          </button>
        );
      })}

      <div className="relative" ref={popoverRef}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="rounded-full border border-dashed px-2 py-0.5 text-2xs transition-colors"
          style={{
            borderColor: '#c0c0c0',
            color: '#616161',
            backgroundColor: 'transparent',
          }}
          title="添加 Reaction"
        >
          ＋
        </button>
        {open && (
          <div
            className="absolute left-0 top-full z-20 mt-1 flex gap-1 rounded-md border p-1 shadow-xl"
            style={{
              backgroundColor: '#f3f3f3',
              borderColor: '#d4d4d4',
            }}
          >
            {EMOJIS.map((emo) => (
              <button
                key={emo}
                type="button"
                onClick={() => {
                  onToggle(emo);
                  setOpen(false);
                }}
                className="rounded px-1.5 py-0.5 text-base transition-transform hover:scale-110"
                style={{ backgroundColor: 'transparent' }}
              >
                {emo}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
