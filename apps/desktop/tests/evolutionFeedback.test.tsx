/**
 * Phase 19 V0 自进化闭环前端回归 —— 消息反馈按钮（FeedbackButtons）。
 *
 * 覆盖：
 *   - 👍 → evolutionFeedback 收到 rating=up + sessionId/messageId
 *   - 👎 → 展开纠错输入 → 提交携带 correction 且 rating=down
 *   - 提交成功 → 按钮区替换为确认文案
 *   - 提交失败 → 静默降级（按钮恢复可点，不抛错）
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/react';

const evolutionFeedback = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    evolutionFeedback: (...args: unknown[]) => evolutionFeedback(...args),
  },
}));

import { FeedbackButtons } from '@/components/chat/FeedbackButtons';

describe('FeedbackButtons（Phase 19 V0）', () => {
  it('👍 提交正向反馈（携带会话与消息标识）', async () => {
    evolutionFeedback.mockClear();
    evolutionFeedback.mockResolvedValue({ ok: true, reflected: false });
    const { getByLabelText, getByText } = render(
      <FeedbackButtons messageId="msg-1" sessionId="run-1" />,
    );
    fireEvent.click(getByLabelText('回答有帮助'));
    await waitFor(() => {
      expect(evolutionFeedback).toHaveBeenCalledWith({
        sessionId: 'run-1',
        messageId: 'msg-1',
        rating: 'up',
      });
    });
    await waitFor(() => expect(getByText('谢谢反馈')).toBeTruthy());
  });

  it('👎 展开纠错输入，提交携带 correction', async () => {
    evolutionFeedback.mockClear();
    evolutionFeedback.mockResolvedValue({ ok: true, reflected: true });
    const { getByLabelText, getByPlaceholderText, getByText } = render(
      <FeedbackButtons messageId="msg-2" sessionId="run-2" />,
    );
    fireEvent.click(getByLabelText('回答有问题'));
    const input = getByPlaceholderText('哪里不对？（可选，帮我会得更好）');
    fireEvent.change(input, { target: { value: '日期范围取错了' } });
    fireEvent.click(getByText('提交'));
    await waitFor(() => {
      expect(evolutionFeedback).toHaveBeenCalledWith({
        sessionId: 'run-2',
        messageId: 'msg-2',
        rating: 'down',
        correction: '日期范围取错了',
      });
    });
    await waitFor(() => expect(getByText('已收到反馈，我会反思改进')).toBeTruthy());
  });

  it('提交失败时静默降级（按钮恢复可点）', async () => {
    evolutionFeedback.mockClear();
    evolutionFeedback.mockRejectedValue(new Error('agent offline'));
    const { getByLabelText, queryByText } = render(
      <FeedbackButtons messageId="msg-3" sessionId="run-3" />,
    );
    fireEvent.click(getByLabelText('回答有帮助'));
    await waitFor(() => expect(evolutionFeedback).toHaveBeenCalledTimes(1));
    // 未进入「已提交」态：按钮仍在，可再次尝试
    expect(queryByText('谢谢反馈')).toBeNull();
    expect(getByLabelText('回答有帮助')).toBeTruthy();
  });
});
