/**
 * 回归测试：App 必须挂载 agent 流订阅（useAgentStream），
 * 否则 Tauri `agent://message` 事件无人接收，聊天 UI 收不到回复（busy 永远卡住）。
 *
 * 复现 bug 的方式：mock Tauri `listen`，渲染真实 <App />，模拟后端事件，
 * 断言事件被路由进 chatStore。
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

describe('agent stream subscription', () => {
  let container: HTMLDivElement;
  let root: Root;

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

  it('App 挂载后，agent://message 事件会被路由到 chatStore（修复“发消息后 UI 无响应”）', async () => {
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

    const emit = listeners.get('agent://message');
    expect(emit).toBeDefined();

    await act(async () => {
      emit!({
        payload: {
          kind: 'message',
          message: { id: 'm-1', role: 'assistant', content: 'hello from agent' },
        },
      });
    });

    const tab = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId);
    expect(tab?.messages.some((m) => m.id === 'm-1' && m.content === 'hello from agent')).toBe(
      true,
    );
  });
});
