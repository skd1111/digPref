/**
 * ClarifyCard —— 模型「选项式追问」交互卡片（2026-08-05）。
 *
 * 渲染在 ChatInput 正上方（视觉连成一体）：
 *   - 每个待确认问题一个页签，按顺序作答（选完自动跳下一题）
 *   - 每题 3-5 个选项，每个选项带选择理由，推荐项打「推荐」标并默认选中
 *   - 每题另有自定义输入框（输入后覆盖选项选择）
 *   - 全部作答后一键发送，回复文本结构化回给模型
 */
import { useMemo, useState } from 'react';
import type { ClarifyQuestion } from '@/lib/clarify';

interface Props {
  questions: ClarifyQuestion[];
  busy: boolean;
  onSend: (text: string) => void;
}

/** 每题初始选中项：推荐项下标；无推荐项则不预选 */
function initialSelections(questions: ClarifyQuestion[]): (number | null)[] {
  return questions.map((q) => {
    const idx = q.options.findIndex((o) => o.recommended);
    return idx >= 0 ? idx : null;
  });
}

export function ClarifyCard({ questions, busy, onSend }: Props): JSX.Element {
  const [selections, setSelections] = useState<(number | null)[]>(() =>
    initialSelections(questions),
  );
  const [customs, setCustoms] = useState<string[]>(() => questions.map(() => ''));
  const [activeTab, setActiveTab] = useState(0);

  /** 每题的最终答案：自定义输入优先，其次所选选项 */
  const answers = useMemo(
    () =>
      questions.map((q, i) => {
        const custom = (customs[i] ?? '').trim();
        if (custom) return custom;
        const sel = selections[i];
        return sel !== null && sel !== undefined && q.options[sel] ? q.options[sel].text : '';
      }),
    [questions, customs, selections],
  );
  const answeredCount = answers.filter((a) => a.length > 0).length;
  const allAnswered = answeredCount === questions.length;

  const pickOption = (qi: number, oi: number): void => {
    setSelections((prev) => prev.map((s, i) => (i === qi ? oi : s)));
    setCustoms((prev) => prev.map((c, i) => (i === qi ? '' : c)));
    // 按顺序作答：自动跳到下一个未回答的页签
    if (qi + 1 < questions.length) setActiveTab(qi + 1);
  };

  const handleSend = (): void => {
    if (!allAnswered || busy) return;
    const lines = questions.map((q, i) => `${i + 1}. ${q.question} → ${answers[i]}`);
    onSend(`[回答确认问题]\n${lines.join('\n')}\n请按以上选择继续。`);
  };

  const tab = Math.min(activeTab, questions.length - 1);
  const q = questions[tab];

  return (
    <div
      className="rounded-t border border-b-0 px-3 py-2"
      style={{ borderColor: '#cecece', backgroundColor: '#ffffff' }}
    >
      {/* 页签（多个问题才显示） */}
      {questions.length > 1 && (
        <div className="mb-2 flex items-center gap-1">
          {questions.map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setActiveTab(i)}
              className="rounded px-2 py-0.5 text-2xs font-medium transition-colors"
              style={{
                backgroundColor: tab === i ? '#007acc' : answers[i] ? '#e8f4e8' : '#ececec',
                color: tab === i ? '#ffffff' : answers[i] ? '#0a7a2f' : '#616161',
                border: '1px solid #d4d4d4',
              }}
            >
              问题 {i + 1}
              {answers[i] ? ' ✓' : ''}
            </button>
          ))}
        </div>
      )}

      {/* 当前问题 */}
      <div className="mb-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
        {questions.length > 1 ? `（${tab + 1}/${questions.length}）` : ''} {q.question}
      </div>

      {/* 选项列表 */}
      <div className="flex flex-col gap-1.5">
        {q.options.map((opt, oi) => {
          const selected = (customs[tab] ?? '').trim() === '' && selections[tab] === oi;
          return (
            <button
              key={oi}
              type="button"
              onClick={() => pickOption(tab, oi)}
              className="rounded border px-2.5 py-1.5 text-left text-ui transition-colors"
              style={{
                borderColor: selected ? '#007acc' : '#d4d4d4',
                backgroundColor: selected ? '#e6f2fb' : '#fafafa',
                color: '#1f1f1f',
              }}
            >
              <span className="flex items-center gap-1.5">
                <span style={{ color: selected ? '#007acc' : '#8a8a8a' }}>
                  {selected ? '◉' : '○'}
                </span>
                <span className="font-medium">{opt.text}</span>
                {opt.recommended && (
                  <span
                    className="rounded px-1 text-2xs font-semibold"
                    style={{ backgroundColor: '#007acc', color: '#ffffff' }}
                  >
                    推荐
                  </span>
                )}
              </span>
              {opt.reason && (
                <span className="mt-0.5 block pl-5 text-2xs" style={{ color: '#616161' }}>
                  {opt.reason}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* 自定义输入 */}
      <textarea
        value={customs[tab] ?? ''}
        onChange={(e) =>
          setCustoms((prev) => prev.map((c, i) => (i === tab ? e.target.value : c)))
        }
        placeholder="或者自定义输入…"
        rows={1}
        className="mt-2 w-full resize-none rounded border px-2 py-1 text-ui focus:outline-none"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff', color: '#1f1f1f' }}
      />

      {/* 底部：进度 + 发送 */}
      <div className="mt-2 flex items-center justify-between">
        <span className="text-2xs" style={{ color: '#616161' }}>
          已回答 {answeredCount}/{questions.length}（推荐项已默认选中，可直接发送或改选）
        </span>
        <button
          type="button"
          onClick={handleSend}
          disabled={!allAnswered || busy}
          className="rounded px-3 py-1 text-2xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
          style={{ backgroundColor: '#007acc' }}
        >
          发送回答
        </button>
      </div>
    </div>
  );
}
