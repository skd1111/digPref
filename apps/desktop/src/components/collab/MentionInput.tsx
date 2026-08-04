/**
 * MentionInput —— 评论输入框 + @ 提及下拉。
 *
 * 行为：
 *   - 纯文本输入，placeholder 提示 Markdown 语法（** / ` / ```）
 *   - 输入 `@` 触发本地用户下拉，键盘上下选择 + Enter 确认
 *   - 选中后插入 `@<user_id> `（不带中文名，name 走渲染时查表）
 *   - 暴露 `onChange(value, mentions)` 给父级
 */
import { useEffect, useRef, useState } from 'react';
import { MOCK_USERS, type CollabUser } from '@/types/collab';

interface MentionInputProps {
  value: string;
  onChange: (value: string, mentions: string[]) => void;
  placeholder?: string;
  rows?: number;
  onSubmit?: () => void;
  onCancel?: () => void;
  autoFocus?: boolean;
}

function extractMentions(text: string): string[] {
  const re = /@([a-z0-9_-]+)/g;
  const out = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    out.add(m[1]);
  }
  return Array.from(out);
}

export function MentionInput({
  value,
  onChange,
  placeholder = '写下你的评论... (Markdown: ** 粗体** / `code` / ```代码块```)',
  rows = 3,
  onSubmit,
  onCancel,
  autoFocus,
}: MentionInputProps): JSX.Element {
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [highlightIdx, setHighlightIdx] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 自动检测 @ 触发
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    const pos = ta.selectionStart;
    const before = value.slice(0, pos);
    const m = before.match(/@([a-z0-9_-]*)$/i);
    if (m) {
      setMentionQuery(m[1].toLowerCase());
      setHighlightIdx(0);
    } else {
      setMentionQuery(null);
    }
  }, [value]);

  const candidates: CollabUser[] =
    mentionQuery === null
      ? []
      : MOCK_USERS.filter(
          (u) =>
            u.id.toLowerCase().includes(mentionQuery) ||
            u.name.toLowerCase().includes(mentionQuery),
        ).slice(0, 5);

  const applyMention = (user: CollabUser): void => {
    const ta = textareaRef.current;
    if (!ta) return;
    const pos = ta.selectionStart;
    const before = value.slice(0, pos);
    const after = value.slice(pos);
    const cleaned = before.replace(/@[a-z0-9_-]*$/i, '');
    const inserted = `@${user.id} `;
    const next = cleaned + inserted + after;
    onChange(next, extractMentions(next));
    requestAnimationFrame(() => {
      const newPos = (cleaned + inserted).length;
      ta.setSelectionRange(newPos, newPos);
      ta.focus();
    });
    setMentionQuery(null);
  };

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (mentionQuery !== null && candidates.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setHighlightIdx((i) => (i + 1) % candidates.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setHighlightIdx((i) => (i - 1 + candidates.length) % candidates.length);
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        applyMention(candidates[highlightIdx]);
        return;
      }
      if (e.key === 'Escape') {
        setMentionQuery(null);
        return;
      }
    }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      onSubmit?.();
    }
    if (e.key === 'Escape') {
      onCancel?.();
    }
  };

  return (
    <div className="relative">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value, extractMentions(e.target.value))}
        onKeyDown={handleKey}
        placeholder={placeholder}
        rows={rows}
        autoFocus={autoFocus}
        className="w-full resize-y rounded-md border p-2 text-ui leading-relaxed focus:outline-none"
        style={{
          backgroundColor: '#ffffff',
          borderColor: '#d4d4d4',
          color: '#1f1f1f',
        }}
      />

      {/* @ 提及下拉 */}
      {mentionQuery !== null && candidates.length > 0 && (
        <div
          className="absolute left-0 top-full z-30 mt-1 max-h-60 overflow-auto rounded-md border shadow-xl"
          style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4', minWidth: 220 }}
        >
          {candidates.map((u, idx) => (
            <button
              key={u.id}
              type="button"
              onClick={() => applyMention(u)}
              onMouseEnter={() => setHighlightIdx(idx)}
              className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-2xs"
              style={{
                backgroundColor: idx === highlightIdx ? 'rgba(99, 102, 241, 0.18)' : 'transparent',
                color: '#1f1f1f',
              }}
            >
              <span
                className="flex h-5 w-5 items-center justify-center rounded-full text-2xs font-semibold text-white"
                style={{ backgroundColor: u.avatar_color }}
              >
                {u.name.charAt(0)}
              </span>
              <span className="flex-1">{u.name}</span>
              <span className="text-2xs" style={{ color: '#616161' }}>
                @{u.id}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* 提示 */}
      <div className="mt-1 flex justify-between text-2xs" style={{ color: '#616161' }}>
        <span>支持 Markdown · @ 提及 · Ctrl+Enter 发送</span>
        <span>{value.length} 字符</span>
      </div>
    </div>
  );
}
