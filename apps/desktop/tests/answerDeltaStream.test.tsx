/**
 * 回答逐字流式（answer_delta，2026-09-03）前端归并测试。
 *
 * 验证：
 * 1. 首帧 answer_delta append 草稿气泡，后续帧按 msgId 原地拼接（不产生第二条）；
 * 2. 终稿 message 事件按同一 msgId update 整条覆盖，草稿收敛为终稿；
 * 3. 带 runId 的 delta 按 run→页签路由，多会话并发不串台。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const { listeners } = vi.hoisted(() => {
  const listeners = new Map<string, (e: { payload: unknown }) => void>();
  return { listeners };
});

vi.mock('@tauri-apps/api/event', () => ({
  listen: async (event: string, handler: (e: { payload: unknown }) => void) => {
    listeners.set(event, handler);
    return () => {
      listeners.delete(event);
    };
  },
}));

import { App } from '@/App';
import { useChatStore } from '@/store/chatStore';

function activeTabMessages() {
  const s = useChatStore.getState();
  return s.tabs.find((t) => t.id === s.activeTabId)?.messages ?? [];
}

describe('answer_delta 回答逐字流式归并', () => {
  let container: HTMLDivElement;
  let root: Root;

  async function mountApp() {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<App />);
    });
    // 等待 subscribeAgentStream 里的 listen() promise 全部 resolve
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) {
      container.remove();
    }
    listeners.clear();
    useChatStore.getState().newTab();
  });

  it('首帧 append 草稿气泡，后续帧按 msgId 原地拼接', async () => {
    await mountApp();
    const emitDelta = listeners.get('agent://answer_delta');
    expect(emitDelta).toBeDefined();

    await act(async () => {
      emitDelta!({ payload: { kind: 'answer_delta', msgId: 'ans-1', delta: '你好' } });
    });
    await act(async () => {
      emitDelta!({ payload: { kind: 'answer_delta', msgId: 'ans-1', delta: '，世界' } });
    });

    const bubbles = activeTabMessages().filter((m) => m.id === 'ans-1');
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].role).toBe('assistant');
    expect(bubbles[0].content).toBe('你好，世界');
  });

  it('终稿 message 按同一 msgId 原地覆盖，草稿收敛不产生第二条', async () => {
    await mountApp();
    const emitDelta = listeners.get('agent://answer_delta')!;
    const emitMessage = listeners.get('agent://message')!;

    await act(async () => {
      emitDelta({ payload: { kind: 'answer_delta', msgId: 'ans-2', delta: '流式草稿' } });
    });
    await act(async () => {
      emitMessage({
        payload: {
          kind: 'message',
          message: { id: 'ans-2', role: 'assistant', content: '流式草稿（终稿精修）' },
        },
      });
    });

    const bubbles = activeTabMessages().filter((m) => m.id === 'ans-2');
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].content).toBe('流式草稿（终稿精修）');
  });

  it('缺 msgId 或 delta 的帧安全忽略', async () => {
    await mountApp();
    const emitDelta = listeners.get('agent://answer_delta')!;
    const before = activeTabMessages().length;

    await act(async () => {
      emitDelta({ payload: { kind: 'answer_delta', delta: '无 id' } });
      emitDelta({ payload: { kind: 'answer_delta', msgId: 'ans-3' } });
    });

    expect(activeTabMessages()).toHaveLength(before);
  });

  it('带 runId 的 delta 按 run→页签路由，不串台', async () => {
    await mountApp();
    const st = useChatStore.getState();
    // 第二个页签 + run 归属登记（模拟另一会话在跑）
    st.newTab('会话B');
    const tabB = useChatStore.getState().activeTabId;
    useChatStore.getState().startRun('run-b', tabB);
    // 切回第一个页签（activeTab 不再是 run-b 的归属页签）
    const tabA = useChatStore.getState().tabs.find((t) => t.id !== tabB)!.id;
    act(() => {
      useChatStore.setState({ activeTabId: tabA });
    });

    const emitDelta = listeners.get('agent://answer_delta')!;
    await act(async () => {
      emitDelta({
        payload: { kind: 'answer_delta', msgId: 'ans-b', delta: 'B 会话的回答', runId: 'run-b' },
      });
    });

    const s = useChatStore.getState();
    const msgsB = s.tabs.find((t) => t.id === tabB)?.messages ?? [];
    const msgsA = s.tabs.find((t) => t.id === tabA)?.messages ?? [];
    expect(msgsB.some((m) => m.id === 'ans-b' && m.content === 'B 会话的回答')).toBe(true);
    expect(msgsA.some((m) => m.id === 'ans-b')).toBe(false);
  });
});
