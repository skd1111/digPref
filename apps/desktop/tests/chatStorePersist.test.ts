/**
 * chatStore 持久化测试（2026-08-07）。
 *
 * 覆盖：tabs 进 localStorage / 运行态字段不进 / 写入裁剪上限 /
 *       恢复时 running 执行步骤降级 ok（避免重启后永久转圈）。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useChatStore } from '@/store/chatStore';
import type { ChatMessage } from '@eaide/shared-protocol';

const STORAGE_KEY = 'eaide-chat-v1';

function persistedState(): Record<string, unknown> | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  return (JSON.parse(raw) as { state?: Record<string, unknown> }).state ?? null;
}

function msg(id: string, role: 'user' | 'assistant', content: string): ChatMessage {
  return { id, role, content };
}

describe('chatStore persist', () => {
  beforeEach(() => {
    localStorage.clear();
    // 复位 store 到干净单 tab
    useChatStore.setState((s) => ({
      tabs: [{ id: 'tab-a', title: '新会话', messages: [] }],
      activeTabId: 'tab-a',
      busy: false,
      runId: null,
      inferenceMode: s.inferenceMode,
    }));
  });

  it('追加消息后 tabs 写入 localStorage', () => {
    useChatStore.getState().append(msg('m1', 'user', '你好'));
    const state = persistedState();
    expect(state).toBeTruthy();
    const tabs = state?.tabs as Array<{ messages: unknown[] }>;
    expect(tabs.length).toBe(1);
    expect(tabs[0].messages.length).toBe(1);
  });

  it('busy / autonomy / runId 不进持久化（运行态重启重置）', () => {
    useChatStore.getState().append(msg('m1', 'user', '你好'));
    const state = persistedState();
    expect(state).toBeTruthy();
    expect('busy' in (state as object)).toBe(false);
    expect('autonomy' in (state as object)).toBe(false);
    expect('runId' in (state as object)).toBe(false);
  });

  it('写入裁剪：单 tab 最多保留最近 500 条消息', () => {
    const many: ChatMessage[] = [];
    for (let i = 0; i < 600; i++) {
      many.push(msg(`m${i}`, 'user', `消息 ${i}`));
    }
    useChatStore.setState((s) => ({
      tabs: s.tabs.map((t) => ({ ...t, messages: many })),
    }));
    const state = persistedState();
    const tabs = state?.tabs as Array<{ messages: Array<{ id: string }> }>;
    expect(tabs[0].messages.length).toBe(500);
    // 保留的是「最近」的：第一条应是 m100
    expect(tabs[0].messages[0].id).toBe('m100');
  });

  it('恢复时卡住的 running 执行步骤降级为 ok', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        state: {
          tabs: [
            {
              id: 'tab-old',
              title: '历史会话',
              messages: [
                { id: 'e1', role: 'system', kind: 'execution', category: 'tool_call', content: '查询', status: 'running' },
                { id: 'm1', role: 'assistant', content: '结果' },
              ],
            },
          ],
          activeTabId: 'tab-old',
          inferenceMode: 'normal',
        },
        version: 0,
      }),
    );
    await useChatStore.persist.rehydrate();
    const s = useChatStore.getState();
    expect(s.activeTabId).toBe('tab-old');
    const exec = s.tabs[0].messages[0];
    expect(exec.status).toBe('ok');
  });

  it('恢复的 activeTabId 不存在时回落到第一个 tab', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        state: {
          tabs: [{ id: 'tab-real', title: '会话', messages: [] }],
          activeTabId: 'tab-ghost',
          inferenceMode: 'normal',
        },
        version: 0,
      }),
    );
    await useChatStore.persist.rehydrate();
    expect(useChatStore.getState().activeTabId).toBe('tab-real');
  });
});

/**
 * 模式隔离（2026-08-11）：专家团（operator）与开发（full）对话各用各的页签组，
 * 修复「专家团模式对话后转开发模式会看到专家团对话」的串场缺陷。
 */
describe('chatStore 模式隔离', () => {
  beforeEach(() => {
    localStorage.clear();
    useChatStore.setState((s) => ({
      tabs: [{ id: 'tab-full', title: '开发会话', messages: [], mode: 'full' as const }],
      activeTabId: 'tab-full',
      busy: false,
      runId: null,
      inferenceMode: s.inferenceMode,
    }));
  });

  it('切到 operator：无页签则新建并激活，full 页签保留', () => {
    useChatStore.getState().ensureModeTab('operator');
    const s = useChatStore.getState();
    expect(s.tabs.length).toBe(2);
    const active = s.tabs.find((t) => t.id === s.activeTabId);
    expect(active?.mode).toBe('operator');
    expect(s.tabs.some((t) => t.id === 'tab-full' && t.mode === 'full')).toBe(true);
  });

  it('切回 full：回到原开发页签，消息不串场', () => {
    useChatStore.getState().ensureModeTab('operator');
    useChatStore.getState().append(msg('op1', 'user', '专家团对话'));
    useChatStore.getState().ensureModeTab('full');
    const s = useChatStore.getState();
    expect(s.activeTabId).toBe('tab-full');
    const fullTab = s.tabs.find((t) => t.id === 'tab-full');
    expect(fullTab?.messages).toEqual([]);
    const opTab = s.tabs.find((t) => t.mode === 'operator');
    expect(opTab?.messages.map((m) => m.id)).toEqual(['op1']);
  });

  it('重复 ensureModeTab 不重复建页签', () => {
    useChatStore.getState().ensureModeTab('operator');
    useChatStore.getState().ensureModeTab('operator');
    expect(useChatStore.getState().tabs.length).toBe(2);
  });

  it('旧数据无 mode 字段按 full 处理（不丢失）', () => {
    useChatStore.setState((s) => ({
      tabs: [{ id: 'tab-legacy', title: '旧会话', messages: [] }],
      activeTabId: 'tab-legacy',
      busy: s.busy,
      runId: null,
      inferenceMode: s.inferenceMode,
    }));
    useChatStore.getState().ensureModeTab('full');
    const s = useChatStore.getState();
    expect(s.tabs.length).toBe(1);
    expect(s.activeTabId).toBe('tab-legacy');
  });

  it('非 operator 模式都归入 full 页签组', () => {
    useChatStore.getState().ensureModeTab('analyst');
    const s = useChatStore.getState();
    expect(s.tabs.length).toBe(1);
    expect(s.activeTabId).toBe('tab-full');
  });

  it('newTab 可带 mode 归属', () => {
    useChatStore.getState().newTab('运营新会话', 'operator');
    const s = useChatStore.getState();
    const created = s.tabs.find((t) => t.id === s.activeTabId);
    expect(created?.mode).toBe('operator');
  });
});
