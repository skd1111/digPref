/**
 * 2026-08-19 回归测试：任务结束汇总展示改动文件列表。
 *
 *   1. useAgentStream 收 builtin_tool_done（write_file / edit_file 成功）累积
 *      改动路径；done 时汇总成 kind='changed_files' 卡片消息追入对话并清空累积。
 *   2. ChatMessage 渲染可点击文件清单，点击 → readTextFile + openFileInEditor。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
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

const readTextFile = vi.fn().mockResolvedValue('public class Foo {}');

// 只覆盖 readTextFile（卡片点击读文件）；其余 ipc 保留真实实现，
// 避免 App 挂载时其它面板的 ipc 调用被 mock 成 undefined
vi.mock('@/ipc/invoke', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/ipc/invoke')>();
  return {
    ...actual,
    ipc: {
      ...actual.ipc,
      readTextFile: (...args: unknown[]) => readTextFile(...args),
    },
  };
});

import { App } from '@/App';
import { ChatMessage } from '@/components/chat/ChatMessage';
import { useChatStore } from '@/store/chatStore';
import { useCodeNavStore } from '@/store/codeNavStore';

describe('任务结束汇总改动文件（流事件 → changed_files 卡片）', () => {
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
    useChatStore.getState().clearChangedFiles();
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

  it('write/edit 成功的 builtin_tool_done 被累积，done 后生成 changed_files 卡片并清空累积', async () => {
    await mountApp();
    const emitToolDone = listeners.get('agent://builtin_tool_done');
    const emitDone = listeners.get('agent://done');
    expect(emitToolDone).toBeDefined();
    expect(emitDone).toBeDefined();

    await act(async () => {
      emitToolDone!({
        payload: {
          kind: 'builtin_tool_done',
          tool_name: 'write_file',
          ok: true,
          result_meta: { path: 'D:/proj/src/Foo.java', bytes_written: 10 },
        },
      });
      emitToolDone!({
        payload: {
          kind: 'builtin_tool_done',
          tool_name: 'edit_file',
          ok: true,
          result_meta: { path: 'D:/proj/src/Bar.java', replacements: 1 },
        },
      });
      // 失败的写操作不收录；read_file 不收录
      emitToolDone!({
        payload: {
          kind: 'builtin_tool_done',
          tool_name: 'write_file',
          ok: false,
          result_meta: { path: 'D:/proj/src/Bad.java' },
        },
      });
      emitToolDone!({
        payload: {
          kind: 'builtin_tool_done',
          tool_name: 'read_file',
          ok: true,
          result_meta: { path: 'D:/proj/src/Read.java' },
        },
      });
      // 同路径重复只记一次
      emitToolDone!({
        payload: {
          kind: 'builtin_tool_done',
          tool_name: 'edit_file',
          ok: true,
          result_meta: { path: 'D:/proj/src/Foo.java' },
        },
      });
    });
    expect(useChatStore.getState().changedFiles).toEqual([
      'D:/proj/src/Foo.java',
      'D:/proj/src/Bar.java',
    ]);

    await act(async () => {
      emitDone!({ payload: { kind: 'done', runId: 'run-x' } });
    });

    const tab = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId);
    const card = tab?.messages.find((m) => m.kind === 'changed_files');
    expect(card).toBeTruthy();
    expect(JSON.parse(card!.content)).toEqual([
      'D:/proj/src/Foo.java',
      'D:/proj/src/Bar.java',
    ]);
    // 累积已清空（下一轮不串）
    expect(useChatStore.getState().changedFiles).toEqual([]);
  });

  it('没有文件改动时 done 不生成卡片', async () => {
    await mountApp();
    const emitDone = listeners.get('agent://done');
    await act(async () => {
      emitDone!({ payload: { kind: 'done', runId: 'run-y' } });
    });
    const tab = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId);
    expect(tab?.messages.some((m) => m.kind === 'changed_files')).toBe(false);
  });
});

describe('ChangedFilesCard 可点击跳转打开', () => {
  beforeEach(() => {
    readTextFile.mockClear();
    useCodeNavStore.setState({ openFiles: [], activeFilePath: null });
  });

  it('渲染文件清单，点击调用 readTextFile 并写入 openFileInEditor', async () => {
    render(
      <ChatMessage
        message={{
          id: 'cf-1',
          role: 'system',
          kind: 'changed_files',
          content: JSON.stringify(['D:/proj/src/Foo.java', 'D:/proj/src/Bar.java']),
        }}
      />,
    );
    expect(screen.getByText(/本次任务改动的文件（2）/)).toBeTruthy();
    expect(screen.getByText('Foo.java')).toBeTruthy();
    expect(screen.getByText('Bar.java')).toBeTruthy();

    fireEvent.click(screen.getByText('Foo.java'));
    await new Promise((r) => setTimeout(r, 0));

    expect(readTextFile).toHaveBeenCalledWith('D:/proj/src/Foo.java');
    const st = useCodeNavStore.getState();
    expect(st.openFiles.some((f) => f.path === 'D:/proj/src/Foo.java')).toBe(true);
    expect(st.activeFilePath).toBe('D:/proj/src/Foo.java');
  });

  it('content 非法 JSON 时渲染空卡片不崩溃', () => {
    const { container } = render(
      <ChatMessage
        message={{ id: 'cf-2', role: 'system', kind: 'changed_files', content: 'not-json' }}
      />,
    );
    expect(container.querySelector('button')).toBeNull();
  });
});
