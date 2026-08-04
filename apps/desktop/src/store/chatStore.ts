/**
 * chatStore — Zustand store for the conversation state.
 *
 * 重要：每个 tab 拥有独立的 messages（多会话线程隔离）。activeTabId 切换时
 * 看到的是那个 tab 的消息流。新建 tab 切换并清空消息。
 *
 * Updated by:
 *   - ChatInput (append user message)
 *   - subscribeAgentStream (append / mutate assistant / tool messages)
 */
import { create } from 'zustand';
import type { ChatMessage } from '@eaide/shared-protocol';
import type { FeatureContextPayload } from '@/types/biznav';

export interface ChatTab {
  id: string;
  title: string;
  messages: ChatMessage[];
}

interface ChatState {
  tabs: ChatTab[];
  activeTabId: string;
  busy: boolean;
  runId: string | null;

  // Phase 2G 业务功能点上下文（由 BiznavChatBridge 写入，headless 订阅者单向桥接）
  selectedFeatureContext: FeatureContextPayload | null;

  // Phase 2D V0 业务技能上下文（由 agentStream 写入，SKILL_MATCHED 事件）
  selectedSkill: { skill_id: string; skill_name: string; matched_keywords: string[] } | null;

  // Phase 4 V0 推理模式：normal = 端侧优先，performance = 全走云端
  inferenceMode: 'normal' | 'performance';

  // Phase 18 会话级自主性：interactive = 每步等人；auto = 按推荐项自动继续。
  // 不持久化（不进 persist）：重启/新会话回落 interactive，需重新开关 + 弹窗确认。
  autonomy: 'interactive' | 'auto';

  // tab 操作
  newTab: (title?: string) => void;
  closeTab: (id: string) => void;
  switchTab: (id: string) => void;
  renameTab: (id: string, title: string) => void;
  /** 把 srcId 拖到 dstId 之前（或末尾如果 dstId 为 null） */
  moveTab: (srcId: string, dstId: string | null) => void;

  // 消息操作（始终作用于 active tab）
  append: (m: ChatMessage) => void;
  /** 追加 Codex/Claude 风格执行 step —— 同 runId+category 自动合并 running → ok */
  appendExecution: (m: ChatMessage) => void;
  update: (id: string, patch: Partial<ChatMessage>) => void;
  setBusy: (b: boolean) => void;
  setRunId: (id: string | null) => void;
  setFeatureContext: (ctx: FeatureContextPayload | null) => void;
  setSelectedSkill: (s: { skill_id: string; skill_name: string; matched_keywords: string[] } | null) => void;

  // Phase 4 V0
  setInferenceMode: (mode: 'normal' | 'performance') => void;
  toggleInferenceMode: () => void;

  // Phase 18
  setAutonomy: (a: 'interactive' | 'auto') => void;
}

const newId = (): string => `tab-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const initialTab = (): ChatTab => ({
  id: newId(),
  title: '新会话',
  messages: [],
});

export const useChatStore = create<ChatState>((set, get) => {
  const t0 = initialTab();
  return {
    tabs: [t0],
    activeTabId: t0.id,
    busy: false,
    runId: null,
    selectedFeatureContext: null,
    selectedSkill: null,
    inferenceMode: 'normal',  // Phase 4 V0: 默认正常模式（端侧优先）
    autonomy: 'interactive',  // Phase 18: 默认交互模式（安全默认值）

    newTab: (title) => {
      const t: ChatTab = { id: newId(), title: title ?? '新会话', messages: [] };
      set((s) => ({ tabs: [...s.tabs, t], activeTabId: t.id, runId: null }));
    },

    closeTab: (id) => {
      const { tabs, activeTabId } = get();
      if (tabs.length <= 1) return; // 至少保留 1 个
      const idx = tabs.findIndex((t) => t.id === id);
      if (idx < 0) return;
      const nextTabs = tabs.filter((t) => t.id !== id);
      const newActive =
        activeTabId === id
          ? nextTabs[Math.min(idx, nextTabs.length - 1)].id
          : activeTabId;
      set({ tabs: nextTabs, activeTabId: newActive, runId: null });
    },

    switchTab: (id) => set({ activeTabId: id, runId: null }),

    renameTab: (id, title) =>
      set((s) => ({
        tabs: s.tabs.map((t) => (t.id === id ? { ...t, title } : t)),
      })),

    moveTab: (srcId, dstId) => {
      const { tabs } = get();
      if (srcId === dstId) return;
      const src = tabs.find((t) => t.id === srcId);
      if (!src) return;
      const without = tabs.filter((t) => t.id !== srcId);
      if (dstId === null) {
        set({ tabs: [...without, src] });
        return;
      }
      const idx = without.findIndex((t) => t.id === dstId);
      if (idx < 0) {
        set({ tabs: [...without, src] });
        return;
      }
      const next = [...without];
      next.splice(idx, 0, src);
      set({ tabs: next });
    },

    append: (m) =>
      set((s) => ({
        tabs: s.tabs.map((t) =>
          t.id === s.activeTabId ? { ...t, messages: [...t.messages, m] } : t,
        ),
      })),

    /**
     * 追加执行链路 step（Codex/Claude 风格）—— 按 runId 折叠到同一块。
     * 同 runId + 同 category 的 running → ok 会更新上一条；不同 category 直接追加。
     */
    appendExecution: (m: ChatMessage) =>
      set((s) => {
        const tab = s.tabs.find((t) => t.id === s.activeTabId);
        if (!tab) return s;
        const list = [...tab.messages];
        // 找同 category 的最近一条 running step 来合并更新
        if (m.status && m.status !== 'running') {
          for (let i = list.length - 1; i >= 0; i--) {
            const prev = list[i];
            if (
              prev.kind === 'execution' &&
              prev.category === m.category &&
              prev.status === 'running'
            ) {
              list[i] = {
                ...prev,
                status: m.status,
                content: m.content,
                ...(m.latencyMs != null ? { latencyMs: m.latencyMs } : {}),
              };
              return { tabs: s.tabs.map((t) => (t.id === tab.id ? { ...t, messages: list } : t)) };
            }
          }
        }
        // 没找到合并目标 → 直接追加（剥离 undefined 字段，绕开 exactOptionalPropertyTypes）
        const clean: ChatMessage = {
          id: m.id,
          role: m.role,
          content: m.content,
          ...(m.code != null ? { code: m.code } : {}),
          ...(m.codeLang != null ? { codeLang: m.codeLang } : {}),
          ...(m.pendingApproval != null ? { pendingApproval: m.pendingApproval } : {}),
          ...(m.kind != null ? { kind: m.kind } : {}),
          ...(m.category != null ? { category: m.category } : {}),
          ...(m.latencyMs != null ? { latencyMs: m.latencyMs } : {}),
          ...(m.status != null ? { status: m.status } : {}),
          ...(m.runId != null ? { runId: m.runId } : {}),
        };
        list.push(clean);
        return { tabs: s.tabs.map((t) => (t.id === tab.id ? { ...t, messages: list } : t)) };
      }),

    update: (id, patch) =>
      set((s) => ({
        tabs: s.tabs.map((t) => {
          if (t.id !== s.activeTabId) return t;
          return {
            ...t,
            messages: t.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
          };
        }),
      })),

    setBusy: (b) => set({ busy: b }),
    setRunId: (id) => set({ runId: id }),
    setSelectedSkill: (s) => set({ selectedSkill: s }),
    setFeatureContext: (ctx) => set({ selectedFeatureContext: ctx }),

    // Phase 4 V0
    setInferenceMode: (mode) => set({ inferenceMode: mode }),
    toggleInferenceMode: () =>
      set((s) => ({
        inferenceMode: s.inferenceMode === 'normal' ? 'performance' : 'normal',
      })),

    // Phase 18
    setAutonomy: (a) => set({ autonomy: a }),
  };
});

// ---------------------------------------------------------------------------
// Phase 2G V1.2：业务功能点上下文 → prompt 注入片段（纯选择器，不消费）
//
// chat 发送消息处调用 `useFeatureContextPromptSnippet()` 拿到待拼到 system
// prompt 前缀的字符串；选中上下文保持显示在 ChatInput 上方的 ContextChip，
// 不因"消费"而清空 —— V0 持续高亮 UX 不变。
//
// 调用方负责判断：若 snippet 为空（无选中 feature），则不注入；非空时拼到
// agent 系统提示词前缀，让 LLM 知道当前讨论的是哪个业务功能点。
// ---------------------------------------------------------------------------

export function useFeatureContextPromptSnippet(): string {
  const ctx = useChatStore((s) => s.selectedFeatureContext);
  if (!ctx) return '';

  const lines: string[] = ['【当前业务功能点上下文】'];
  lines.push(`- 功能：${ctx.feature_name} (${ctx.feature_id})`);
  if (ctx.feature_description) {
    lines.push(`- 描述：${ctx.feature_description}`);
  }
  if (ctx.related_files.length > 0) {
    lines.push('- 关联文件：');
    for (const f of ctx.related_files.slice(0, 10)) {
      lines.push(`  - ${f.path}`);
    }
    if (ctx.related_files.length > 10) {
      lines.push(`  - …（还有 ${ctx.related_files.length - 10} 个）`);
    }
  }
  if (ctx.related_apis.length > 0) {
    lines.push('- 关联 API：');
    for (const a of ctx.related_apis.slice(0, 5)) {
      lines.push(`  - ${a.method} ${a.path}`);
    }
  }
  if (ctx.related_tables.length > 0) {
    lines.push('- 关联表：');
    for (const t of ctx.related_tables.slice(0, 5)) {
      lines.push(`  - ${t.name}`);
    }
  }
  if (ctx.business_rules.length > 0) {
    lines.push('- 业务规则：');
    for (const r of ctx.business_rules.slice(0, 10)) {
      lines.push(`  - ${r}`);
    }
  }
  lines.push('');
  lines.push('（以上为运营专家模式选中的业务功能点上下文，请在回答时主动结合；如用户提出与该功能无关的问题，可正常脱离上下文。）');
  return lines.join('\n');
}
