/**
 * 任务进度待办列表（2026-08-25）前端回归：
 *   - TodoCard：进度条/三态渲染/脏 JSON 兜底
 *   - chatStore.upsertTodo：固定 id 原地更新（同一任务始终一张卡）
 *   - BUGFIX #169：todo 事件按 run 归属写页签，不串激活页签（A 会话跑任务时
 *     切到 B 会话，计划卡不得串到 B）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

import { TodoCard } from '@/components/chat/TodoCard';
import { LeftTaskPlanPanel } from '@/components/chat/LeftTaskPlanPanel';
import { useChatStore } from '@/store/chatStore';
import { useUIStore } from '@/store/uiStore';
import { useAgentStream } from '@/hooks/useAgentStream';

describe('TodoCard', () => {
  let container: HTMLDivElement;
  let root: Root;

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) container.remove();
  });

  async function render(itemsJson: string): Promise<void> {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<TodoCard itemsJson={itemsJson} />);
    });
  }

  it('渲染进度条与三态条目', async () => {
    await render(
      JSON.stringify([
        { content: '收集资料', status: 'done' },
        { content: '生成大纲', status: 'in_progress' },
        { content: '导出文件', status: 'pending' },
      ]),
    );
    const text = container.textContent ?? '';
    expect(text).toContain('任务进度');
    expect(text).toContain('1/3');
    expect(text).toContain('33%');
    expect(text).toContain('收集资料');
    expect(text).toContain('生成大纲');
    expect(text).toContain('导出文件');
    // in_progress 用 spinner 细环（不用图标字符）
    expect(container.querySelector('.animate-spin-ring')).toBeTruthy();
  });

  it('脏 JSON / 空列表不渲染（不炸页面）', async () => {
    await render('这不是 JSON');
    expect(container.textContent ?? '').toBe('');
    await act(async () => {
      root.unmount();
    });
    container.remove();
    await render('[]');
    expect(container.textContent ?? '').toBe('');
  });
});

describe('chatStore.upsertTodo', () => {
  it('首次追加、后续原地更新（不新增气泡）', () => {
    useChatStore.setState({
      tabs: [
        { id: 'tab-1', title: 't', messages: [] },
        { id: 'tab-2', title: 't2', messages: [] },
      ],
      activeTabId: 'tab-1',
    });
    const v1 = JSON.stringify([{ content: '第一步', status: 'in_progress' }]);
    const v2 = JSON.stringify([{ content: '第一步', status: 'done' }]);

    // BUGFIX #169：写入指定页签，即便它不是激活页签（这里写 tab-2）
    useChatStore.getState().upsertTodo('tab-2', 'todo-run-1', v1);
    const tabs = useChatStore.getState().tabs;
    expect(tabs[0].messages).toHaveLength(0); // 激活页签不动
    expect(tabs[1].messages).toHaveLength(1);
    expect(tabs[1].messages[0].kind).toBe('todo');
    expect(tabs[1].messages[0].content).toBe(v1);

    useChatStore.getState().upsertTodo('tab-2', 'todo-run-1', v2);
    const msgs = useChatStore.getState().tabs[1].messages;
    expect(msgs.length).toBe(1); // 原地更新，不是第二张卡
    expect(msgs[0].content).toBe(v2);
  });
});

describe('todo 卡按 run 归属路由（BUGFIX #169：不串激活页签）', () => {
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
    useChatStore.getState().newTab('会话 A');
  });

  afterEach(() => {
    root?.unmount();
    root = null;
    listeners.clear();
  });

  async function mount(): Promise<void> {
    root = createRoot(document.createElement('div'));
    await act(async () => {
      root!.render(<StreamApp />);
    });
  }

  function StreamApp(): JSX.Element {
    useAgentStream();
    return <div />;
  }

  it('A 会话跑任务 + B 会话激活 → todo 事件写进 A，不碰 B', async () => {
    await mount();
    const s = useChatStore.getState();
    const tabA = s.activeTabId;
    s.newTab('会话 B');
    const tabB = useChatStore.getState().activeTabId;
    useChatStore.getState().startRun('run-A', tabA);

    const emitTrace = listeners.get('agent://trace')!;
    expect(emitTrace).toBeDefined();
    await act(async () => {
      emitTrace({
        payload: {
          kind: 'trace',
          runId: 'run-A',
          step: {
            id: 'tr-1',
            node: 'todo',
            summary: '更新待办',
            status: 'ok',
            todos: [{ content: '收集资料', status: 'in_progress' }],
          },
        },
      });
    });

    const st = useChatStore.getState();
    const msgsA = st.tabs.find((t) => t.id === tabA)?.messages ?? [];
    const msgsB = st.tabs.find((t) => t.id === tabB)?.messages ?? [];
    // 归属页签 A 收到卡（关掉 B 切回来仍在）；激活页签 B 不被污染
    expect(msgsA.some((m) => m.kind === 'todo')).toBe(true);
    expect(msgsB.some((m) => m.kind === 'todo')).toBe(false);
  });
});

describe('左侧任务计划面板（2026-08-28）', () => {
  let container: HTMLDivElement;
  let root: Root;

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) container.remove();
  });

  async function render(itemsJson: string): Promise<void> {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<TodoCard itemsJson={itemsJson} />);
    });
  }

  it('竖向布局：标题行与进度条分行展示，宽度自适应（无固定上限）', async () => {
    await render(
      JSON.stringify([
        { content: '第一步', status: 'done' },
        { content: '第二步', status: 'pending' },
      ]),
    );
    const text = container.textContent ?? '';
    expect(text).toContain('任务进度');
    expect(text).toContain('1/2');
    expect(text).toContain('50%');
    // 竖向：进度条独占一行（w-full），不再限宽 420 横排挤压
    const bar = container.querySelector('.h-1\\.5.w-full');
    expect(bar).toBeTruthy();
  });
});

describe('LeftTaskPlanPanel（读激活页签最新 todo）', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    useChatStore.setState({ tabs: [], activeTabId: '' });
  });

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) container.remove();
  });

  async function mount(): Promise<void> {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<LeftTaskPlanPanel />);
    });
  }

  it('无计划时显示空态；激活页签有 todo 后实时渲染进度卡', async () => {
    useChatStore.setState({
      tabs: [
        { id: 'tab-1', title: 't', messages: [] },
        { id: 'tab-2', title: 't2', messages: [] },
      ],
      activeTabId: 'tab-1',
    });
    await mount();
    expect(container.textContent ?? '').toContain('暂无任务计划');
    expect(container.textContent ?? '').not.toContain('任务进度');

    // 计划到达（写入激活页签）→ 面板实时切到进度卡（其它页签的卡不串入）
    await act(async () => {
      useChatStore.getState().upsertTodo(
        'tab-1',
        'todo-run-1',
        JSON.stringify([{ content: '收集资料', status: 'in_progress' }]),
      );
      useChatStore.getState().upsertTodo(
        'tab-2',
        'todo-run-2',
        JSON.stringify([{ content: '另一个会话的计划', status: 'pending' }]),
      );
    });
    const text = container.textContent ?? '';
    expect(text).toContain('任务进度');
    expect(text).toContain('收集资料');
    expect(text).not.toContain('另一个会话的计划');
  });
});

describe('uiStore 左侧面板状态（2026-08-28）', () => {
  it('宽度钳制在 200–600；leftView 可切换', () => {
    useUIStore.getState().setLeftPanelWidth(50);
    expect(useUIStore.getState().leftPanelWidth).toBe(200);
    useUIStore.getState().setLeftPanelWidth(999);
    expect(useUIStore.getState().leftPanelWidth).toBe(600);
    useUIStore.getState().setLeftPanelWidth(320);
    expect(useUIStore.getState().leftPanelWidth).toBe(320);

    useUIStore.getState().setLeftView('plan');
    expect(useUIStore.getState().leftView).toBe('plan');
    useUIStore.getState().setLeftView('explorer');
    expect(useUIStore.getState().leftView).toBe('explorer');
  });
});
