/**
 * 写前 Diff 预览测试（执行过程可视化 · 阶段四）。
 *
 *   1. file_write_preview 事件 → 预览卡进执行链路 + diff 存 store；
 *   2. builtin_tool_done（带 call_id）→ 对应预览卡翻牌成终态；
 *   3. WritePreviewCard 渲染 +/- 统计（数据来自 store）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
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
import { WritePreviewCard } from '@/components/chat/WritePreviewCard';
import { useChatStore } from '@/store/chatStore';

const SAMPLE_DIFF = [
  '--- a/demo.txt',
  '+++ b/demo.txt',
  '@@ -1,2 +1,3 @@',
  ' keep',
  '-old line',
  '+new line',
  '+added line',
].join('\n');

describe('写前 Diff 预览（流事件 → 预览卡 → 翻牌）', () => {
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
    useChatStore.setState({ writePreviewByCall: {} });
  });

  async function mountApp(): Promise<void> {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<App />);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it('file_write_preview → 预览卡入执行链路且 diff 进 store', async () => {
    await mountApp();
    const emit = listeners.get('agent://file_write_preview');
    expect(emit).toBeDefined();
    await act(async () => {
      emit!({
        payload: {
          kind: 'file_write_preview',
          call_id: 'call-w1',
          path: '/tmp/demo.txt',
          diff: SAMPLE_DIFF,
          risk_level: 'medium',
        },
      });
    });
    const st = useChatStore.getState();
    expect(st.writePreviewByCall['call-w1']?.diff).toBe(SAMPLE_DIFF);
    const tab = st.tabs.find((t) => t.id === st.activeTabId);
    expect(tab?.messages.some((m) => m.id === 'preview-call-w1')).toBe(true);
  });

  it('builtin_tool_done 带 call_id → 预览卡翻牌为终态', async () => {
    await mountApp();
    const emitPreview = listeners.get('agent://file_write_preview')!;
    const emitDone = listeners.get('agent://builtin_tool_done')!;
    await act(async () => {
      emitPreview({
        payload: { kind: 'file_write_preview', call_id: 'call-w2', path: '/tmp/a.txt', diff: SAMPLE_DIFF },
      });
    });
    await act(async () => {
      emitDone({
        payload: { kind: 'builtin_tool_done', tool_name: 'write_file', ok: true, call_id: 'call-w2' },
      });
    });
    const st = useChatStore.getState();
    const tab = st.tabs.find((t) => t.id === st.activeTabId);
    const msg = tab?.messages.find((m) => m.id === 'preview-call-w2');
    expect(msg?.status).toBe('ok');
  });

  it('WritePreviewCard 渲染 +/- 统计（跳过 +++/--- 头部行）', () => {
    act(() => {
      useChatStore.getState().setWritePreview('call-card', '/tmp/demo.txt', SAMPLE_DIFF);
    });
    render(<WritePreviewCard callId="call-card" />);
    expect(screen.getByText('写前预览')).toBeTruthy();
    expect(screen.getByText('+2')).toBeTruthy();
    expect(screen.getByText('-1')).toBeTruthy();
    expect(screen.getByText('查看完整 Diff')).toBeTruthy();
  });

  it('无 diff 数据时 WritePreviewCard 不渲染', () => {
    const { container: c } = render(<WritePreviewCard callId="call-none" />);
    expect(c.innerHTML).toBe('');
  });
});
