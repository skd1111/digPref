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
import { useUIStore } from '@/store/uiStore';

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

  it('done/error 双发幂等（BUGFIX #150）：Rust 桥转发后端事件后又补发 stream_closed，只生效一次', async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => {
      root.render(<App />);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const emitError = listeners.get('agent://error');
    expect(emitError).toBeDefined();

    // 模拟一次运行：发送时登记 run→页签归属（多会话并发后归属表即幂等键）
    useChatStore.getState().startRun('run-dup-1', useChatStore.getState().activeTabId);

    // 同一错误到达两次（后端 error 事件 + 桥层连接错误补发）
    await act(async () => {
      emitError!({ payload: { kind: 'error', message: 'boom' } });
    });
    await act(async () => {
      emitError!({ payload: { kind: 'error', message: 'boom' } });
    });

    const tab = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId);
    const errorCards = (tab?.messages ?? []).filter((m) => m.kind === 'error');
    // 只追加一张错误卡；归属已清（首个 error 消费）；全局运行态解除
    expect(errorCards).toHaveLength(1);
    expect(errorCards[0].content).toContain('boom');
    expect(useChatStore.getState().busy).toBe(false);
    expect(useChatStore.getState().runId).toBeNull();

    // done 同理：无 runId 时（已被消费）第二个 done 不重复归档，也不报错
    const emitDone = listeners.get('agent://done');
    await act(async () => {
      emitDone!({ payload: { kind: 'done', reason: 'stream_closed' } });
      emitDone!({ payload: { kind: 'done', reason: 'stream_closed' } });
    });
    expect(useChatStore.getState().busy).toBe(false);
  });

  it('rag_retrieve trace 与知识检索工具调用不进对话（2026-08-17 隐藏「检索知识库」卡片），执行链路不变', async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => {
      root.render(<App />);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const emit = listeners.get('agent://message');
    expect(emit).toBeDefined();

    await act(async () => {
      // 后端 rag_retrieve 节点照常执行并下发 trace（前端只不展示）
      emit!({
        payload: {
          kind: 'trace',
          step: { id: 't-rag', node: 'rag_retrieve', status: 'ok', summary: '检索知识库' },
        },
      });
      // 知识检索类工具调用 / 结果同样不进对话
      emit!({
        payload: { kind: 'tool_call', id: 'c-rag', call: { name: 'knowledge.retrieve' } },
      });
      emit!({
        payload: {
          kind: 'tool_result',
          id: 'c-rag',
          result: { name: 'knowledge.retrieve', ok: true },
        },
      });
      // 对照：联网搜索仍展示搜索卡片（不误伤其他搜索类工具）
      emit!({ payload: { kind: 'tool_call', id: 'c-web', call: { name: 'web_search' } } });
      // 对照：普通工具仍走执行链路块
      emit!({
        payload: { kind: 'tool_call', id: 'c-sql', call: { name: 'database.query' } },
      });
    });

    const tab = useChatStore
      .getState()
      .tabs.find((t) => t.id === useChatStore.getState().activeTabId);
    const msgs = tab?.messages ?? [];
    // 无「检索知识库」搜索卡片（trace 与工具链路都不注入）
    expect(msgs.some((m) => m.kind === 'search' && m.content.includes('检索知识库'))).toBe(false);
    expect(msgs.some((m) => m.content.includes('knowledge.retrieve'))).toBe(false);
    // 联网搜索卡片保留
    expect(msgs.some((m) => m.id === 'c-web' && m.kind === 'search')).toBe(true);
    // 普通工具执行链路块保留
    expect(msgs.some((m) => m.id === 'c-sql' && m.kind === 'execution')).toBe(true);
  });

  it('skill_matched 不再写输入框上方徽标态，改为对话流内执行步骤卡，同 skill 重命中不刷屏', async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => {
      root.render(<App />);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const emit = listeners.get('agent://skill_matched');
    expect(emit).toBeDefined();

    await act(async () => {
      emit!({ payload: { kind: 'skill_matched', skill_id: 'sk-1', skill_name: '坏账分析' } });
      // 追问轮同 skill 再次命中 → 原地翻牌，不追加第二张卡
      emit!({ payload: { kind: 'skill_matched', skill_id: 'sk-1', skill_name: '坏账分析' } });
    });

    const state = useChatStore.getState();
    const tab = state.tabs.find((t) => t.id === state.activeTabId);
    const skillSteps = (tab?.messages ?? []).filter(
      (m) => m.kind === 'execution' && m.category === 'skill_matched',
    );
    // 对话流内只有一张技能加载步骤卡，正文含技能名，状态为完成态
    expect(skillSteps).toHaveLength(1);
    expect(skillSteps[0].content).toContain('坏账分析');
    expect(skillSteps[0].status).toBe('ok');
    // skill 粘性照旧（追问轮透传依赖），旧徽标态已下线（store 无该字段）
    expect(state.lastSkillId).toBe('sk-1');
    expect('selectedSkill' in state).toBe(false);
  });

  it('首次出现任务计划时，左侧停在资源管理器视角自动切到任务计划；进度刷新/手动切回不再强切', async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => {
      root.render(<App />);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    // 前置：开发模式 + explorer activity + 资源管理器视角（默认态）
    useUIStore.setState({ mode: 'full', activityId: 'explorer', leftView: 'explorer' });

    const emit = listeners.get('agent://trace');
    expect(emit).toBeDefined();

    const todoPayload = {
      kind: 'trace',
      runId: 'run-todo-1',
      step: {
        id: 't-todo-1',
        node: 'todo',
        status: 'ok',
        todos: [{ content: '第一步', status: 'pending' }],
      },
    };

    // 首次下发 → 计划卡新建，左侧自动切到任务计划
    useChatStore.getState().startRun('run-todo-1', useChatStore.getState().activeTabId);
    await act(async () => {
      emit!({ payload: todoPayload });
    });
    expect(useUIStore.getState().leftView).toBe('plan');

    // 进度刷新（原地更新同一张卡）：用户手动切回资源管理器后不再被强切
    useUIStore.setState({ leftView: 'explorer' });
    await act(async () => {
      emit!({
        payload: {
          ...todoPayload,
          step: { ...todoPayload.step, todos: [{ content: '第一步', status: 'done' }] },
        },
      });
    });
    expect(useUIStore.getState().leftView).toBe('explorer');

    // 还原，避免污染同文件其它用例（leftView 持久化到 localStorage）
    useUIStore.setState({ leftView: 'explorer' });
  });
});
