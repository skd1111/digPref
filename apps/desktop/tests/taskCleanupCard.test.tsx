/**
 * 交付后验收清理卡回归（2026-08-26）。
 *
 * 用户要求：运行期间产生的文件都在任务文件夹里；做完用户验收后询问
 * 是否把除产物之外的文件清理掉。
 * 覆盖：
 *   1. useAgentStream done 事件携带 taskId/taskDir 且本轮有产物 → 追入
 *      kind='task_cleanup_confirm' 卡片；无产物不打扰；
 *   2. TaskCleanupCard 拉取清单展示产物/中间文件，〔清理中间文件〕调
 *      ipc.taskCleanup（保留产物），〔全部保留〕写回决策状态；
 *   3. 决策后卡片折叠为结果文案（组件重挂不丢状态，走 store）。
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

const { taskFilesGet, taskCleanup } = vi.hoisted(() => ({
  taskFilesGet: vi.fn().mockResolvedValue({
    task_dir: 'D:/eaide/workspace/tasks/20260826-101010_ppt',
    task_dir_exists: true,
    artifacts: ['D:/eaide/workspace/tasks/20260826-101010_ppt/docs/eaide_intro.pptx'],
    intermediates: ['D:/eaide/workspace/tasks/20260826-101010_ppt/scratch.json'],
  }),
  taskCleanup: vi.fn().mockResolvedValue({
    ok: true,
    deleted: ['D:/eaide/workspace/tasks/20260826-101010_ppt/scratch.json'],
    kept: ['D:/eaide/workspace/tasks/20260826-101010_ppt/docs/eaide_intro.pptx'],
    task_dir_removed: false,
  }),
}));

vi.mock('@/ipc/invoke', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/ipc/invoke')>();
  return {
    ...actual,
    ipc: {
      ...actual.ipc,
      taskFilesGet,
      taskCleanup,
      sessionsAppendMessage: vi.fn().mockResolvedValue(undefined),
    },
  };
});

import { useChatStore } from '@/store/chatStore';
import { TaskCleanupCard } from '@/components/chat/TaskCleanupCard';
import { useAgentStream } from '@/hooks/useAgentStream';

function App(): JSX.Element {
  useAgentStream();
  return <div />;
}

let root: Root | null = null;

beforeEach(() => {
  taskFilesGet.mockClear();
  taskCleanup.mockClear();
  // 干净 store：一个激活页签（并发改造后按 run 隔离的运行态字段全部置空）
  useChatStore.setState({
    tabs: [],
    busy: false,
    runId: null,
    busyTabIds: [],
    runTabMap: {},
    tabRunIds: {},
    runPhaseByRun: {},
    changedFilesByRun: {},
    artifactsByRun: {},
    runStartTsByRun: {},
  });
  useChatStore.getState().newTab('测试会话');
});

afterEach(() => {
  root?.unmount();
  root = null;
});

describe('done 事件弹验收清理卡', () => {
  it('有产物且 done 带 taskId/taskDir → 追入 task_cleanup_confirm 卡片', async () => {
    root = createRoot(document.createElement('div'));
    await act(async () => {
      root!.render(<App />);
    });
    // 模拟一轮执行：登记 run 归属 + 累积产物（产物按 run 隔离）
    const tabId = useChatStore.getState().activeTabId;
    useChatStore.getState().startRun('run-t1', tabId);
    useChatStore.getState().addTaskArtifact('run-t1', 'D:/x/docs/a.pptx');

    const emitDone = listeners.get('agent://done');
    expect(emitDone).toBeTruthy();
    await act(async () => {
      emitDone!({
        payload: {
          kind: 'done',
          runId: 'run-t1',
          taskId: 'tab-task',
          taskDir: 'D:/eaide/workspace/tasks/20260826-101010_ppt',
        },
      });
    });
    const tab = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId);
    const card = tab?.messages.find((m) => m.kind === 'task_cleanup_confirm');
    expect(card).toBeTruthy();
    expect(JSON.parse(card!.content)).toMatchObject({ taskId: 'tab-task' });
  });

  it('纯问答轮（无产物）不打扰', async () => {
    root = createRoot(document.createElement('div'));
    await act(async () => {
      root!.render(<App />);
    });
    const tabId = useChatStore.getState().activeTabId;
    useChatStore.getState().startRun('run-t2', tabId);

    const emitDone = listeners.get('agent://done');
    await act(async () => {
      emitDone!({
        payload: { kind: 'done', runId: 'run-t2', taskId: 'tab-task', taskDir: 'D:/x' },
      });
    });
    const tab = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId);
    expect(tab?.messages.some((m) => m.kind === 'task_cleanup_confirm')).toBe(false);
  });
});

describe('TaskCleanupCard 交互', () => {
  function appendCleanupMsg(): { id: string; content: string } {
    const msg = {
      id: 'tc-1',
      role: 'system' as const,
      kind: 'task_cleanup_confirm' as const,
      status: 'running' as const,
      content: JSON.stringify({
        taskId: 'tab-task',
        taskDir: 'D:/eaide/workspace/tasks/20260826-101010_ppt',
      }),
    };
    useChatStore.getState().append(msg);
    return msg;
  }

  it('展示产物与中间文件统计，清理调用保留产物', async () => {
    const msg = appendCleanupMsg();
    await act(async () => {
      render(<TaskCleanupCard message={msg as never} />);
    });
    expect(taskFilesGet).toHaveBeenCalledWith('tab-task');
    expect(screen.getByText('eaide_intro.pptx')).toBeTruthy();
    expect(screen.getByText(/1 个中间文件/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /清理中间文件/ }));
    });
    expect(taskCleanup).toHaveBeenCalledWith('tab-task', [
      'D:/eaide/workspace/tasks/20260826-101010_ppt/docs/eaide_intro.pptx',
    ]);
    // 决策写回 store（组件重挂不丢状态）
    const stored = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId)
      ?.messages.find((m) => m.id === 'tc-1');
    expect(stored?.status).toBe('ok');
    expect(stored?.category).toBe('cleaned:1');
  });

  it('全部保留 → 记录 kept 且不再清理', async () => {
    const msg = appendCleanupMsg();
    await act(async () => {
      render(<TaskCleanupCard message={msg as never} />);
    });
    await act(async () => {
      fireEvent.click(screen.getByText('全部保留'));
    });
    expect(taskCleanup).not.toHaveBeenCalled();
    const stored = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId)
      ?.messages.find((m) => m.id === 'tc-1');
    expect(stored?.status).toBe('ok');
    expect(stored?.category).toBe('kept');
  });

  it('已决策卡片重挂后直接渲染结果文案', async () => {
    appendCleanupMsg();
    useChatStore.getState().update('tc-1', { status: 'ok', category: 'kept' });
    const tab = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId);
    const stored = tab!.messages.find((m) => m.id === 'tc-1')!;
    await act(async () => {
      render(<TaskCleanupCard message={stored as never} />);
    });
    expect(screen.getByText(/已保留本任务的全部文件/)).toBeTruthy();
    expect(taskFilesGet).not.toHaveBeenCalled();
  });
});
