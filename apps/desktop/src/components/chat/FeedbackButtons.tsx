/**
 * FeedbackButtons — 助手终答的 👍/👎 反馈按钮（Phase 19 V0 自进化闭环）。
 *
 * 用户显式反馈是自评测的最高置信信号（设计文档 §2.2）：
 *   - 👍 → 记录正向信号
 *   - 👎 → 记录负向信号 + 可选纠错文本 → 后端后台触发反思提炼经验
 *
 * 反馈经 Rust `evolution_feedback` 透传到 /evolution/feedback；
 * 提交失败静默降级（不阻塞对话，按钮恢复可点）。
 */
import { useState } from 'react';
import { ipc } from '@/ipc/invoke';

interface Props {
  /** 消息 id（归因到具体回复） */
  messageId: string;
  /** 会话 run_id（轨迹归属；缺失时后端按最近轨迹兜底） */
  sessionId: string;
}

export function FeedbackButtons({ messageId, sessionId }: Props): JSX.Element {
  const [choice, setChoice] = useState<'up' | 'down' | null>(null);
  const [showCorrection, setShowCorrection] = useState(false);
  const [correction, setCorrection] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [sending, setSending] = useState(false);

  const submit = async (rating: 'up' | 'down', note: string): Promise<void> => {
    if (sending) return;
    setSending(true);
    try {
      await ipc.evolutionFeedback({
        sessionId,
        messageId,
        rating,
        ...(note.trim() ? { correction: note.trim() } : {}),
      });
      setChoice(rating);
      setSubmitted(true);
      setShowCorrection(false);
    } catch {
      // best-effort：反馈失败不影响对话；按钮恢复可点
      setSending(false);
      return;
    }
    setSending(false);
  };

  if (submitted) {
    return (
      <div className="mt-1 text-[10px]" style={{ color: '#9ca3af' }}>
        {choice === 'down' ? '已收到反馈，我会反思改进' : '谢谢反馈'}
      </div>
    );
  }

  return (
    <div className="mt-1 flex items-center gap-2 opacity-0 transition-opacity group-hover:opacity-100">
      <button
        type="button"
        title="回答有帮助"
        aria-label="回答有帮助"
        disabled={sending}
        onClick={() => void submit('up', '')}
        className="rounded border px-1.5 py-0.5 text-[11px] transition-colors hover:bg-[#f5f5f4] disabled:opacity-40"
        style={{ borderColor: '#e7e5e4', backgroundColor: '#ffffff' }}
      >
        👍
      </button>
      <button
        type="button"
        title="回答有问题"
        aria-label="回答有问题"
        disabled={sending}
        onClick={() => setShowCorrection((v) => !v)}
        className="rounded border px-1.5 py-0.5 text-[11px] transition-colors hover:bg-[#f5f5f4] disabled:opacity-40"
        style={{
          borderColor: showCorrection ? '#f59e0b' : '#e7e5e4',
          backgroundColor: '#ffffff',
        }}
      >
        👎
      </button>
      {showCorrection && (
        <span className="flex items-center gap-1">
          <input
            value={correction}
            onChange={(e) => setCorrection(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void submit('down', correction);
            }}
            placeholder="哪里不对？（可选，帮我会得更好）"
            className="rounded border px-1.5 py-0.5 text-[11px] outline-none"
            style={{ borderColor: '#d4d4d4', width: 200 }}
          />
          <button
            type="button"
            disabled={sending}
            onClick={() => void submit('down', correction)}
            className="rounded border px-1.5 py-0.5 text-[11px] transition-colors hover:bg-[#f5f5f4] disabled:opacity-40"
            style={{ borderColor: '#e7e5e4', backgroundColor: '#ffffff' }}
          >
            提交
          </button>
        </span>
      )}
    </div>
  );
}
