/**
 * TabBar AI 标题摘要测试（2026-08-07 功能）。
 *
 * 覆盖：
 *   - 首轮对话完成（user + assistant 都有、busy=false）→ 调 summarizeTitle，
 *     成功后用 AI 摘要替换自动截断标题
 *   - busy=true 时跳过（等流结束后再摘要）
 *   - 手动改过的标题不被覆盖
 */
import { act, render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TabBar } from '@/components/chat/TabBar';
import { useChatStore } from '@/store/chatStore';

const summarizeTitle = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    summarizeTitle: (...args: unknown[]) => summarizeTitle(...args),
  },
}));

function seedTab(
  messages: Array<{ id: string; role: 'user' | 'assistant'; content: string }>,
  title = '新会话',
  busy = false,
): string {
  const id = `tab-${Date.now()}-${Math.random()}`;
  useChatStore.setState({ tabs: [{ id, title, messages }], activeTabId: id, busy });
  return id;
}

const USER_TEXT = '帮我分析一下订单超时率升高的原因';
const FIRST_ROUND = [
  { id: 'u1', role: 'user' as const, content: USER_TEXT },
  { id: 'a1', role: 'assistant' as const, content: '已分析，超时率升高主要来自下游接口…' },
];

describe('TabBar AI 标题摘要', () => {
  beforeEach(() => {
    summarizeTitle.mockReset();
    summarizeTitle.mockResolvedValue({ title: '智能摘要标题' });
  });

  it('首轮对话完成后用 AI 摘要替换截断标题', async () => {
    const tabId = seedTab(FIRST_ROUND);
    render(<TabBar />);

    await waitFor(() => expect(summarizeTitle).toHaveBeenCalledTimes(1));
    expect(summarizeTitle).toHaveBeenCalledWith(USER_TEXT, expect.stringContaining('已分析'));
    await waitFor(() => {
      expect(useChatStore.getState().tabs.find((t) => t.id === tabId)?.title).toBe(
        '智能摘要标题',
      );
    });
  });

  it('busy=true 时等待流结束后再摘要', async () => {
    seedTab(FIRST_ROUND, '新会话', true);
    render(<TabBar />);

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(summarizeTitle).not.toHaveBeenCalled();

    await act(async () => {
      useChatStore.setState({ busy: false });
    });
    await waitFor(() => expect(summarizeTitle).toHaveBeenCalledTimes(1));
  });

  it('手动改过的标题不被 AI 摘要覆盖', async () => {
    const tabId = seedTab(FIRST_ROUND, '我的自定义标题');
    render(<TabBar />);

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(summarizeTitle).not.toHaveBeenCalled();
    expect(useChatStore.getState().tabs.find((t) => t.id === tabId)?.title).toBe(
      '我的自定义标题',
    );
  });
});
