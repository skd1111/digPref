/**
 * 输入框历史记录快捷输入测试（2026-08-26）。
 *
 * 覆盖：
 *   - buildUserHistory 纯函数（角色过滤 / 空白过滤 / 连续去重 / 时序）
 *   - ChatInput：空框 ↑ 进入浏览并回填最新历史；↑/↓ 翻页；↓ 到底与 Esc 恢复草稿；
 *     非浏览态非空输入不劫持 ↑↓；切页签复位浏览态
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { ChatMessage } from '@eaide/shared-protocol';

function msg(id: string, role: 'user' | 'assistant', content: string): ChatMessage {
  return { id, role, content };
}

// ---- 纯函数 ------------------------------------------------------------------

import { buildUserHistory } from '@/lib/chatHistory';

describe('buildUserHistory 纯函数', () => {
  it('只取 user 角色、过滤空白、保留时序', () => {
    const out = buildUserHistory([
      msg('m1', 'user', '第一条'),
      msg('m2', 'assistant', '回答'),
      { id: 'm3', role: 'system', kind: 'execution', category: 'log', content: 'sys', status: 'ok' },
      msg('m4', 'user', '   '),
      msg('m5', 'user', '第二条'),
    ]);
    expect(out).toEqual(['第一条', '第二条']);
  });

  it('连续重复只留一条（非连续不去重）', () => {
    const out = buildUserHistory([
      msg('m1', 'user', 'a'),
      msg('m2', 'user', 'a'),
      msg('m3', 'user', 'b'),
      msg('m4', 'user', 'a'),
    ]);
    expect(out).toEqual(['a', 'b', 'a']);
  });

  it('空消息列表返空数组', () => {
    expect(buildUserHistory([])).toEqual([]);
  });
});

// ---- ChatInput 交互 -----------------------------------------------------------

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    routerListBackends: vi.fn().mockResolvedValue({ backends: [] }),
    chatCompressHistory: vi.fn(),
    chatAttachFile: vi.fn(),
    cancel: vi.fn(),
    sessionsCreate: vi.fn().mockResolvedValue({ id: 'sess-test' }),
    sessionsAppendMessage: vi.fn().mockResolvedValue(undefined),
    biznavProfile: vi.fn().mockResolvedValue({ has_profile: false, profile: '' }),
  },
  invoke: vi.fn().mockResolvedValue('run-test'),
}));

import { ChatInput } from '@/components/chat/ChatInput';
import { useChatStore } from '@/store/chatStore';

describe('ChatInput 历史记录快捷输入', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    localStorage.clear();
    useChatStore.setState((s) => ({
      tabs: [
        {
          id: 'tab-a',
          title: '新会话',
          messages: [
            msg('m1', 'user', '旧问题'),
            msg('m2', 'assistant', '旧回答'),
            msg('m3', 'user', '新问题'),
          ],
        },
      ],
      activeTabId: 'tab-a',
      busy: false,
      runId: null,
      inferenceMode: s.inferenceMode,
    }));
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

  async function render(): Promise<HTMLTextAreaElement> {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<ChatInput />);
    });
    const ta = container.querySelector('textarea');
    expect(ta).toBeTruthy();
    return ta!;
  }

  async function press(ta: HTMLTextAreaElement, key: string): Promise<void> {
    await act(async () => {
      ta.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
  }

  it('空输入框按 ↑ 回填最新历史，再按 ↑ 翻向更早', async () => {
    const ta = await render();
    expect(ta.value).toBe('');
    await press(ta, 'ArrowUp');
    expect(ta.value).toBe('新问题');
    await press(ta, 'ArrowUp');
    expect(ta.value).toBe('旧问题');
    // 到最早一条后再按 ↑ 停在原地
    await press(ta, 'ArrowUp');
    expect(ta.value).toBe('旧问题');
  });

  it('浏览中按 ↓ 翻回，到底后恢复进入前草稿', async () => {
    const ta = await render();
    await press(ta, 'ArrowUp'); // 新问题
    await press(ta, 'ArrowUp'); // 旧问题
    await press(ta, 'ArrowDown'); // 新问题
    expect(ta.value).toBe('新问题');
    await press(ta, 'ArrowDown'); // 到底 → 草稿（进入时为空）
    expect(ta.value).toBe('');
    // 退出浏览后 ↓ 不再劫持（空历史动作）
    await press(ta, 'ArrowDown');
    expect(ta.value).toBe('');
  });

  it('Esc 退出浏览并恢复草稿', async () => {
    const ta = await render();
    await press(ta, 'ArrowUp');
    expect(ta.value).toBe('新问题');
    await press(ta, 'Escape');
    expect(ta.value).toBe('');
  });

  it('非浏览态且输入框非空时 ↑ 不劫持（保留原生光标移动）', async () => {
    const ta = await render();
    await act(async () => {
      // 直接设受控值：模拟用户正在打字
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
      setter.call(ta, '正在编辑的内容');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(ta.value).toBe('正在编辑的内容');
    await press(ta, 'ArrowUp');
    expect(ta.value).toBe('正在编辑的内容'); // 未被历史替换
  });

  it('无历史时按 ↑ 无任何效果', async () => {
    useChatStore.setState((s) => ({
      tabs: s.tabs.map((t) => ({ ...t, messages: [] })),
    }));
    const ta = await render();
    await press(ta, 'ArrowUp');
    expect(ta.value).toBe('');
  });

  it('切页签复位浏览态（新页签 ↑ 从自己的历史最新条开始）', async () => {
    const ta = await render();
    await press(ta, 'ArrowUp');
    expect(ta.value).toBe('新问题');
    // 切到另一个页签（有独立历史）
    await act(async () => {
      useChatStore.setState((s) => ({
        tabs: [
          ...s.tabs,
          { id: 'tab-b', title: '另一会话', messages: [msg('b1', 'user', '别页签的问题')] },
        ],
        activeTabId: 'tab-b',
      }));
    });
    expect(ta.value).toBe('新问题'); // 文本不跨页签搬运；非空时 ↑ 仍不劫持
    await press(ta, 'ArrowUp');
    expect(ta.value).toBe('新问题');
    // 清空输入框后 ↑ 从新页签自己的历史最新条开始（浏览态已复位，不续旧位置）
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
      setter.call(ta, '');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await press(ta, 'ArrowUp');
    expect(ta.value).toBe('别页签的问题');
  });
});
