/**
 * 活跃实体上下文测试（2026-08-31）：意图识别四层增强 · 上下文感知。
 *
 * 覆盖：
 *   - ChatInput 发送时 pageContext.page.activeEntity 携带数据工作台当前选中的表
 *   - 未选中表时 activeEntity 为 null（后端据此跳过实体注入）
 *
 * 后端将 activeEntity 压成「当前正查看数据表 xxx」注入意图分析，
 * 让「删掉最后一条」这类短句直接锁定目标实体。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    routerListBackends: vi.fn().mockResolvedValue({ backends: [] }),
    routerGetGenLimits: vi
      .fn()
      .mockResolvedValue({ ok: true, limits: { max_output_tokens: 4096, default_context_window: 4096 } }),
    chatCompressHistory: vi.fn().mockResolvedValue({ ok: true, summary: '', beforeTokens: 0, afterTokens: 0, messageCount: 0 }),
    chatAttachFile: vi.fn(),
    cancel: vi.fn(),
    sessionsCreate: vi.fn().mockResolvedValue({ id: 'sess-test' }),
    sessionsAppendMessage: vi.fn().mockResolvedValue(undefined),
    biznavProfile: vi.fn().mockResolvedValue({ has_profile: false, profile: '' }),
  },
  invoke: vi.fn().mockResolvedValue('run-test'),
}));

import { ChatInput } from '@/components/chat/ChatInput';
import { invoke } from '@/ipc/invoke';
import { useChatStore } from '@/store/chatStore';
import { useDataStore } from '@/store/dataStore';

describe('ChatInput 活跃实体注入（pageContext.activeEntity）', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    localStorage.clear();
    (invoke as unknown as ReturnType<typeof vi.fn>).mockClear();
    useChatStore.setState((s) => ({
      tabs: [{ id: 'tab-a', title: '新会话', messages: [] }],
      activeTabId: 'tab-a',
      busy: false,
      runId: null,
      inferenceMode: s.inferenceMode,
    }));
    useDataStore.setState({ selectedTable: null, selectedSourceId: null });
  });

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) container.remove();
    useDataStore.setState({ selectedTable: null, selectedSourceId: null });
    useChatStore.setState((s) => ({
      tabs: [{ id: 'tab-a', title: '新会话', messages: [] }],
      activeTabId: 'tab-a',
      busy: false,
      runId: null,
      inferenceMode: s.inferenceMode,
      busyTabIds: [],
      runTabMap: {},
      tabRunIds: {},
      runPhaseByRun: {},
      changedFilesByRun: {},
      artifactsByRun: {},
      runStartTsByRun: {},
    }));
  });

  async function render(): Promise<void> {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<ChatInput />);
    });
  }

  async function send(text: string): Promise<void> {
    const ta = container.querySelector('textarea')!;
    const setValue = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!;
    await act(async () => {
      setValue.call(ta, text);
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const sendBtn = Array.from(container.querySelectorAll('button')).find((b) => b.title === '发送')!;
    await act(async () => {
      sendBtn.click();
    });
  }

  function agentChatArgs(): Record<string, unknown> | undefined {
    const calls = (invoke as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
      (c) => c[0] === 'agent_chat'
    );
    return calls[0]?.[1] as Record<string, unknown> | undefined;
  }

  it('数据工作台选中表时，pageContext 携带 activeEntity={kind:table,name}', async () => {
    useDataStore.getState().selectTable('order_main');
    await render();
    await send('删掉最后一条');

    const args = agentChatArgs();
    expect(args).toBeTruthy();
    const pageContext = args!.pageContext as { page: { activeEntity?: unknown } };
    expect(pageContext.page.activeEntity).toEqual({ kind: 'table', name: 'order_main' });
  });

  it('未选中表时，activeEntity 为 null', async () => {
    await render();
    await send('你好');

    const args = agentChatArgs();
    expect(args).toBeTruthy();
    const pageContext = args!.pageContext as { page: { activeEntity?: unknown } };
    expect(pageContext.page.activeEntity).toBeNull();
  });
});
