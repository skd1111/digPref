/**
 * CommentEditor —— 评论编辑器容器（顶层评论 + Thread 回复都复用）。
 *
 * 组成：MentionInput + Markdown 预览切换 + PII 提示 banner + 提交/取消按钮。
 */
import { useState } from 'react';
import { MentionInput } from './MentionInput';
import { MarkdownRenderer } from './MarkdownRenderer';

interface CommentEditorProps {
  onSubmit: (content: string, mentions: string[]) => void;
  onCancel?: () => void;
  /** 占位（Thread 回复时换成"回复 @xxx"） */
  placeholder?: string;
  rows?: number;
  autoFocus?: boolean;
  /** PII 提示（demo 用：评论含卡号 / 身份证时顶部黄色提示） */
  showPiiWarning?: boolean;
}

function detectPii(text: string): boolean {
  // 简单规则：13-19 位连续数字 OR 18 位身份证 OR 11 位手机
  return /\b\d{13,19}\b/.test(text) || /\b\d{17}[\dXx]\b/.test(text) || /\b1[3-9]\d{9}\b/.test(text);
}

export function CommentEditor({
  onSubmit,
  onCancel,
  placeholder,
  rows = 3,
  autoFocus,
  showPiiWarning = true,
}: CommentEditorProps): JSX.Element {
  const [text, setText] = useState('');
  const [mentions, setMentions] = useState<string[]>([]);
  const [showPreview, setShowPreview] = useState(false);

  const pii = showPiiWarning && detectPii(text);

  const handleSubmit = (): void => {
    if (text.trim().length === 0) return;
    onSubmit(text.trim(), mentions);
    setText('');
    setMentions([]);
    setShowPreview(false);
  };

  return (
    <div
      className="rounded-md border p-3"
      style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4' }}
    >
      {/* PII 提示 banner */}
      {pii && (
        <div
          className="mb-2 flex items-start gap-2 rounded p-2 text-2xs"
          style={{
            backgroundColor: 'rgba(220, 220, 170, 0.12)',
            borderLeft: '3px solid #dcdcaa',
            color: '#795e26',
          }}
        >
          <span>⚠</span>
          <div>
            <strong>检测到可能的 PII（卡号 / 身份证 / 手机号）</strong>。
            真实生产中 <strong>脱敏由后端 redact.py 处理</strong>，前端仅展示。
            最终落库前内容会经过：PII 脱敏 → SHA-256 hash → AES-256-GCM 加密。
          </div>
        </div>
      )}

      {/* 预览 / 编辑 切换 */}
      {showPreview ? (
        <div
          className="min-h-[80px] rounded border p-2"
          style={{ backgroundColor: '#ffffff', borderColor: '#d4d4d4' }}
        >
          {text.trim() ? (
            <MarkdownRenderer content={text} />
          ) : (
            <span className="text-2xs" style={{ color: '#616161' }}>
              （空内容）
            </span>
          )}
        </div>
      ) : (
        <MentionInput
          value={text}
          onChange={(v, ms) => {
            setText(v);
            setMentions(ms);
          }}
          {...(placeholder ? { placeholder } : {})}
          rows={rows}
          onSubmit={handleSubmit}
          {...(onCancel ? { onCancel } : {})}
          {...(autoFocus ? { autoFocus } : {})}
        />
      )}

      {/* 操作栏 */}
      <div className="mt-2 flex items-center justify-between">
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => setShowPreview((v) => !v)}
            className="rounded px-2 py-0.5 text-2xs transition-colors"
            style={{
              backgroundColor: showPreview ? 'rgba(99, 102, 241, 0.18)' : 'transparent',
              color: showPreview ? '#4f46e5' : '#616161',
              border: '1px solid #d4d4d4',
            }}
          >
            {showPreview ? '✏ 编辑' : '👁 预览'}
          </button>
        </div>
        <div className="flex gap-2">
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="rounded px-3 py-1 text-2xs transition-colors"
              style={{
                backgroundColor: 'transparent',
                color: '#616161',
                border: '1px solid #d4d4d4',
              }}
            >
              取消
            </button>
          )}
          <button
            type="button"
            onClick={handleSubmit}
            disabled={text.trim().length === 0}
            className="rounded px-3 py-1 text-2xs font-semibold transition-colors"
            style={{
              backgroundColor: text.trim().length === 0 ? '#ececec' : '#6366f1',
              color: text.trim().length === 0 ? '#616161' : '#ffffff',
              cursor: text.trim().length === 0 ? 'not-allowed' : 'pointer',
            }}
          >
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
