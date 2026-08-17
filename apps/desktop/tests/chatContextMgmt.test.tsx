/**
 * chat 上下文管理测试（2026-08-17）：大小显示 + 断点式清理 + LLM 压缩。
 *
 * 覆盖：
 *   - estimateTokens / tabContextMessages / estimateHistoryTokens 纯函数
 *   - chatStore.clearTabContext（断点设到最后一条 + 作废旧摘要）
 *   - chatStore.applyTabCompression（摘要 + 断点移到保留消息首条）
 *   - contextBreakpoint / contextSummary 随 tabs 持久化
 *   - ChatInput：🧠 指示器展示估算 token；菜单「清理上下文」「压缩上下文」行为
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { ChatMessage } from '@eaide/shared-protocol';

const STORAGE_KEY = 'eaide-chat-v1';

function msg(id: string, role: 'user' | 'assistant', content: string): ChatMessage {
  return { id, role, content };
}

// ---- 纯函数与 store 层 ------------------------------------------------------

import {
  useChatStore,
  estimateTokens,
  tabContextMessages,
  estimateHistoryTokens,
  type ChatTab,
} from '@/store/chatStore';

describe('上下文大小估算与断点过滤（纯函数）', () => {
  it('estimateTokens 按 ~4 字符/token 估算且至少为 1', () => {
    expect(estimateTokens('')).toBe(1);
    expect(estimateTokens('abcd')).toBe(1);
    expect(estimateTokens('abcdefgh')).toBe(2);
  });

  it('tabContextMessages 无断点时取全部 user/assistant 消息', () => {
    const tab: ChatTab = {
      id: 't',
      title: 'x',
      messages: [
        msg('m1', 'user', 'aaaa'),
        { id: 'm2', role: 'system', kind: 'execution', category: 'log', content: 'sys', status: 'ok' },
        msg('m3', 'assistant', 'bbbbbbbb'),
      ],
    };
    const out = tabContextMessages(tab);
    expect(out.map((m) => m.id)).toEqual(['m1', 'm3']);
    expect(estimateHistoryTokens(tab)).toBe(3); // 1 + 2
  });

  it('tabContextMessages 排除断点及之前的消息', () => {
    const tab: ChatTab = {
      id: 't',
      title: 'x',
      messages: [msg('m1', 'user', 'aaaa'), msg('m2', 'assistant', 'bbbb'), msg('m3', 'user', 'cccc')],
      contextBreakpoint: 'm2',
    };
    const out = tabContextMessages(tab);
    expect(out.map((m) => m.id)).toEqual(['m3']);
    expect(estimateHistoryTokens(tab)).toBe(1);
  });

  it('断点 id 不存在时（消息被删）回落全量，不抛错', () => {
    const tab: ChatTab = {
      id: 't',
      title: 'x',
      messages: [msg('m1', 'user', 'aaaa')],
      contextBreakpoint: 'ghost',
    };
    expect(tabContextMessages(tab).length).toBe(1);
  });
});

describe('chatStore 断点清理与压缩应用', () => {
  beforeEach(() => {
    localStorage.clear();
    useChatStore.setState((s) => ({
      tabs: [
        {
          id: 'tab-a',
          title: '新会话',
          messages: [msg('m1', 'user', 'aaaa'), msg('m2', 'assistant', 'bbbb'), msg('m3', 'user', 'cccc')],
          contextSummary: '旧摘要',
        },
      ],
      activeTabId: 'tab-a',
      busy: false,
      runId: null,
      inferenceMode: s.inferenceMode,
    }));
  });

  it('clearTabContext 把断点设到最后一条消息并作废旧摘要', () => {
    useChatStore.getState().clearTabContext('tab-a');
    const tab = useChatStore.getState().tabs[0];
    expect(tab.contextBreakpoint).toBe('m3');
    expect(tab.contextSummary).toBeUndefined();
    expect(tabContextMessages(tab)).toEqual([]);
  });

  it('空会话 clearTabContext 不设断点', () => {
    useChatStore.setState((s) => ({
      tabs: s.tabs.map((t) => ({ ...t, messages: [] })),
    }));
    useChatStore.getState().clearTabContext('tab-a');
    expect(useChatStore.getState().tabs[0].contextBreakpoint).toBeUndefined();
  });

  it('applyTabCompression 写入摘要并把断点移到保留消息首条', () => {
    useChatStore.getState().applyTabCompression('tab-a', '新摘要', 'm2');
    const tab = useChatStore.getState().tabs[0];
    expect(tab.contextSummary).toBe('新摘要');
    expect(tab.contextBreakpoint).toBe('m2');
    expect(tabContextMessages(tab).map((m) => m.id)).toEqual(['m3']);
  });

  it('contextBreakpoint / contextSummary 随 tabs 持久化', () => {
    useChatStore.getState().applyTabCompression('tab-a', '新摘要', 'm2');
    const raw = localStorage.getItem(STORAGE_KEY);
    expect(raw).toBeTruthy();
    const state = (JSON.parse(raw!) as { state: { tabs: Array<Record<string, unknown>> } }).state;
    expect(state.tabs[0].contextBreakpoint).toBe('m2');
    expect(state.tabs[0].contextSummary).toBe('新摘要');
  });
});

// ---- ChatInput UI -----------------------------------------------------------

const chatCompressHistory = vi.fn().mockResolvedValue({
  ok: true,
  summary: '压缩后的摘要',
  beforeTokens: 120,
  afterTokens: 25,
  messageCount: 2,
});

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    routerListBackends: vi.fn().mockResolvedValue({ backends: [] }),
    chatCompressHistory: (...args: unknown[]) => chatCompressHistory(...args),
    chatAttachFile: vi.fn(),
    cancel: vi.fn(),
  },
  invoke: vi.fn(),
}));

import { ChatInput } from '@/components/chat/ChatInput';

/** 构造 N 轮对话（每轮 user+assistant 各 8 字符 = 各 2 tok） */
function turns(n: number, offset = 0): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (let i = 0; i < n; i++) {
    out.push(msg(`u${offset + i}`, 'user', 'abcd1234'));
    out.push(msg(`a${offset + i}`, 'assistant', 'efgh5678'));
  }
  return out;
}

describe('ChatInput 上下文指示器与清理/压缩菜单', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    localStorage.clear();
    chatCompressHistory.mockClear();
  });

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) container.remove();
    useChatStore.setState((s) => ({
      tabs: [{ id: 'tab-a', title: '新会话', messages: [] }],
      activeTabId: 'tab-a',
      busy: false,
      runId: null,
      inferenceMode: s.inferenceMode,
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

  function setMessages(messages: ChatMessage[]): void {
    useChatStore.setState((s) => ({
      tabs: s.tabs.map((t) => ({ ...t, messages })),
    }));
  }

  function allButtons(el: HTMLElement): HTMLButtonElement[] {
    return Array.from(el.querySelectorAll('button'));
  }

  it('🧠 指示器展示将发送的会话 history 估算 token', async () => {
    setMessages(turns(2)); // 4 条 × 2 tok = 8 tok
    await render();
    const indicator = allButtons(container).find((b) => b.textContent?.includes('tok'));
    expect(indicator).toBeTruthy();
    expect(indicator!.textContent).toContain('≈8 tok');
  });

  it('清理上下文：断点生效 + 插分隔线 + 指示器归零', async () => {
    setMessages(turns(2));
    await render();
    // 打开菜单
    const indicator = allButtons(container).find((b) => b.textContent?.includes('tok'))!;
    await act(async () => {
      indicator.click();
    });
    const clearBtn = allButtons(document.body).find((b) => b.textContent?.includes('清理上下文'));
    expect(clearBtn).toBeTruthy();
    await act(async () => {
      clearBtn!.click();
    });

    const tab = useChatStore.getState().tabs[0];
    // 断点 = 分隔线本身（appendChat 在前）：其后无任何 user/assistant 消息
    expect(tab.contextBreakpoint?.startsWith('ctx-clear-')).toBe(true);
    expect(tab.messages.some((m) => m.content.includes('上下文已清理'))).toBe(true);
    expect(estimateHistoryTokens(tab)).toBe(0);
  });

  it('压缩上下文：保留最近 5 轮，其余进 LLM 摘要', async () => {
    setMessages(turns(7)); // 14 条；保留 10 条 → 压缩前 4 条（2 轮）
    await render();
    const indicator = allButtons(container).find((b) => b.textContent?.includes('tok'))!;
    await act(async () => {
      indicator.click();
    });
    const compressBtn = allButtons(document.body).find((b) => b.textContent?.includes('压缩上下文'));
    expect(compressBtn).toBeTruthy();
    await act(async () => {
      compressBtn!.click();
    });

    expect(chatCompressHistory).toHaveBeenCalledTimes(1);
    const body = chatCompressHistory.mock.calls[0][0] as { messages: Array<{ role: string }> };
    expect(body.messages.length).toBe(4);
    expect(body.messages.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant']);

    const tab = useChatStore.getState().tabs[0];
    expect(tab.contextSummary).toBe('压缩后的摘要');
    expect(tab.contextBreakpoint).toBe('a1'); // 最后一条被压缩的消息
    // 保留的 5 轮仍在上下文中
    expect(tabContextMessages(tab).length).toBe(10);
    // 消息流插入压缩分隔线
    expect(tab.messages.some((m) => m.content.includes('上下文已压缩'))).toBe(true);
  });

  it('历史不足 5 轮时压缩按钮禁用', async () => {
    setMessages(turns(4)); // 8 条 ≤ 10
    await render();
    const indicator = allButtons(container).find((b) => b.textContent?.includes('tok'))!;
    await act(async () => {
      indicator.click();
    });
    const compressBtn = allButtons(document.body).find((b) => b.textContent?.includes('压缩上下文'));
    expect(compressBtn).toBeTruthy();
    expect(compressBtn!.disabled).toBe(true);
  });

  it('压缩失败：内联报错且不清数据', async () => {
    chatCompressHistory.mockRejectedValueOnce(new Error('llm down'));
    setMessages(turns(7));
    await render();
    const indicator = allButtons(container).find((b) => b.textContent?.includes('tok'))!;
    await act(async () => {
      indicator.click();
    });
    const compressBtn = allButtons(document.body).find((b) => b.textContent?.includes('压缩上下文'))!;
    await act(async () => {
      compressBtn.click();
    });
    expect(container.textContent).toContain('llm down');
    const tab = useChatStore.getState().tabs[0];
    expect(tab.contextSummary).toBeUndefined();
    expect(tab.contextBreakpoint).toBeUndefined();
  });
});
