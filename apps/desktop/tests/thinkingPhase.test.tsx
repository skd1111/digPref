/**
 * 「思考中」细化 + 多会话并发回归（2026-08-26，用户反馈「思考中太宽泛」+ 并发执行）。
 *
 * 覆盖：
 *   1. builtin_tool_started → 该 run 阶段切「工具调用中：某动作」；
 *      builtin_tool_done → 回「等模型返回」，成功工具回执「做完了：某动作」；
 *   2. done 按 run 清归属（其他并发 run 不受影响）；
 *   3. 工具调用块翻牌（BUGFIX #157：不再永远「进行中」转圈）；
 *   4. 并发两个 run 各归各的页签，事件不串台。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from '@testing-library/react';
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

vi.mock('@/ipc/invoke', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/ipc/invoke')>();
  return {
    ...actual,
    ipc: {
      ...actual.ipc,
      sessionsAppendMessage: vi.fn().mockResolvedValue(undefined),
    },
  };
});

import { useChatStore } from '@/store/chatStore';
import { useAgentStream } from '@/hooks/useAgentStream';

function App(): JSX.Element {
  useAgentStream();
  return <div />;
}

let root: Root | null = null;

beforeEach(() => {
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

async function mount(): Promise<void> {
  root = createRoot(document.createElement('div'));
  await act(async () => {
    root!.render(<App />);
  });
}

function activeTabId(): string {
  return useChatStore.getState().activeTabId;
}

function activeTabMessages() {
  const s = useChatStore.getState();
  return s.tabs.find((t) => t.id === s.activeTabId)?.messages ?? [];
}

describe('执行阶段细化（按 run 隔离）', () => {
  it('工具开始 → 「工具调用中：中文动作」；工具完成 → 回「等模型返回」', async () => {
    await mount();
    const tabId = activeTabId();
    useChatStore.getState().startRun('run-1', tabId);
    const started = listeners.get('agent://builtin_tool_started')!;
    const done = listeners.get('agent://builtin_tool_done')!;

    await act(async () => {
      started({ payload: { kind: 'builtin_tool_started', tool_name: 'write_file', runId: 'run-1' } });
    });
    expect(useChatStore.getState().runPhaseByRun['run-1']).toEqual({
      phase: 'tool',
      detail: '写入文件',
    });

    await act(async () => {
      done({
        payload: { kind: 'builtin_tool_done', tool_name: 'write_file', ok: true, result_meta: {}, runId: 'run-1' },
      });
    });
    expect(useChatStore.getState().runPhaseByRun['run-1']?.phase).toBe('model');
  });

  it('工具做完回执人性化进度；伪工具不刷屏；失败不回复执', async () => {
    await mount();
    const tabId = activeTabId();
    useChatStore.getState().startRun('run-2', tabId);
    const done = listeners.get('agent://builtin_tool_done')!;

    await act(async () => {
      done({
        payload: { kind: 'builtin_tool_done', tool_name: 'office_create', ok: true, result_meta: {}, runId: 'run-2' },
      });
      done({
        payload: { kind: 'builtin_tool_done', tool_name: 'update_todos', ok: true, result_meta: {}, runId: 'run-2' },
      });
      done({
        payload: { kind: 'builtin_tool_done', tool_name: 'shell', ok: false, result_meta: {}, runId: 'run-2' },
      });
    });

    const receipts = activeTabMessages().filter((m) => m.category === 'step_done');
    expect(receipts).toHaveLength(1);
    expect(receipts[0].content).toContain('做完了：创建 Office 文档');
  });
});

describe('run 归属生命周期（多会话并发基础）', () => {
  it('startRun 登记归属；done 后只清自己，不误伤其他并发 run', async () => {
    await mount();
    const s = useChatStore.getState();
    const tabA = s.activeTabId;
    s.newTab('第二个会话');
    const tabB = useChatStore.getState().activeTabId;

    useChatStore.getState().startRun('run-A', tabA);
    useChatStore.getState().startRun('run-B', tabB);
    expect(useChatStore.getState().busyTabIds).toEqual([tabA, tabB]);
    expect(useChatStore.getState().busy).toBe(true);

    const emitDone = listeners.get('agent://done')!;
    await act(async () => {
      emitDone({ payload: { kind: 'done', runId: 'run-A' } });
    });
    // run-A 清归属：页签 A 解锁；run-B 不受影响
    const st = useChatStore.getState();
    expect(st.busyTabIds).toEqual([tabB]);
    expect(st.runTabMap['run-A']).toBeUndefined();
    expect(st.runTabMap['run-B']).toBe(tabB);
    expect(st.busy).toBe(true);

    await act(async () => {
      emitDone({ payload: { kind: 'done', runId: 'run-A' } }); // 双发幂等
      emitDone({ payload: { kind: 'done', runId: 'run-B' } });
    });
    const st2 = useChatStore.getState();
    expect(st2.busyTabIds).toEqual([]);
    expect(st2.busy).toBe(false);
  });

  it('并发两个 run 的消息各归各的页签，不串台', async () => {
    await mount();
    const s = useChatStore.getState();
    const tabA = s.activeTabId;
    s.newTab('第二个会话');
    const tabB = useChatStore.getState().activeTabId;
    useChatStore.getState().startRun('run-A', tabA);
    useChatStore.getState().startRun('run-B', tabB);

    const emitMsg = listeners.get('agent://message')!;
    await act(async () => {
      emitMsg({
        payload: {
          kind: 'message',
          message: { id: 'mA', role: 'assistant', content: 'A 的回答', runId: 'run-A' },
        },
      });
      emitMsg({
        payload: {
          kind: 'message',
          message: { id: 'mB', role: 'assistant', content: 'B 的回答', runId: 'run-B' },
        },
      });
    });

    const st = useChatStore.getState();
    const msgsA = st.tabs.find((t) => t.id === tabA)?.messages ?? [];
    const msgsB = st.tabs.find((t) => t.id === tabB)?.messages ?? [];
    expect(msgsA.some((m) => m.id === 'mA')).toBe(true);
    expect(msgsA.some((m) => m.id === 'mB')).toBe(false);
    expect(msgsB.some((m) => m.id === 'mB')).toBe(true);
    expect(msgsB.some((m) => m.id === 'mA')).toBe(false);
  });
});

describe('工具调用块翻牌（BUGFIX #157：不再永远「进行中」转圈）', () => {
  it('tool_result 按工具名把同名 running 调用块翻成完成/失败', async () => {
    await mount();
    const tabId = activeTabId();
    useChatStore.getState().startRun('run-3', tabId);
    const call = listeners.get('agent://tool_call')!;
    const result = listeners.get('agent://tool_result')!;

    await act(async () => {
      call({ payload: { kind: 'tool_call', id: 'c-1', call: { name: 'shell' }, runId: 'run-3' } });
    });
    await act(async () => {
      result({
        payload: { kind: 'tool_result', id: 'r-另一个', result: { name: 'shell', ok: true }, runId: 'run-3' },
      });
    });

    const blocks = activeTabMessages().filter(
      (m) => m.kind === 'execution' && m.category === 'tool_call',
    );
    // 原调用块原地翻牌为完成，无永远转圈的残留
    expect(blocks).toHaveLength(1);
    expect(blocks[0].status).toBe('ok');
    expect(blocks.some((m) => m.status === 'running')).toBe(false);

    // 失败同理翻成失败态
    await act(async () => {
      call({ payload: { kind: 'tool_call', id: 'c-2', call: { name: 'office_edit' }, runId: 'run-3' } });
    });
    await act(async () => {
      result({
        payload: {
          kind: 'tool_result',
          id: 'r-3',
          result: { name: 'office_edit', ok: false },
          runId: 'run-3',
        },
      });
    });
    const failed = activeTabMessages().find(
      (m) => m.category === 'tool_call' && m.content === 'office_edit',
    );
    expect(failed?.status).toBe('err');
  });
});
