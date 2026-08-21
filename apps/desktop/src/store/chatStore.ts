/**
 * chatStore — Zustand store for the conversation state.
 *
 * 重要：每个 tab 拥有独立的 messages（多会话线程隔离）。activeTabId 切换时
 * 看到的是那个 tab 的消息流。新建 tab 切换并清空消息。
 *
 * 持久化（2026-08-07）：tabs / activeTabId / inferenceMode 进 localStorage，
 * 重启不再丢对话。busy / runId / autonomy / 各类上下文不进 persist（运行态
 * 与安全默认值重启重置）。写入前裁剪：最多 20 个 tab，每 tab 最近 500 条消息。
 *
 * Updated by:
 *   - ChatInput (append user message)
 *   - subscribeAgentStream (append / mutate assistant / tool messages)
 */
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { ChatMessage } from '@eaide/shared-protocol';
import type { FeatureContextPayload } from '@/types/biznav';
import type { Skill } from '@/types/skill';
import type { ExpertTeam } from '@/types/expertTeam';
import type { WorkMode } from '@/store/uiStore';
import { useSkillsStore } from '@/store/skillsStore';
import { useExpertTeamStore } from '@/store/expertTeamStore';

export interface ChatTab {
  id: string;
  title: string;
  messages: ChatMessage[];
  /** 后端 sessions 归档用的 session id（2026-08-07，首次发送时懒创建） */
  backendSessionId?: string;
  /** 页签所属模式（2026-08-11 模式隔离）；旧数据无值按 'full' 处理 */
  mode?: WorkMode;
  /** 纯净对话模式（2026-08-14）：发送时不注入任何项目上下文（画像/功能点/专家团/对齐参照），
   *  用于与工程无关的闲聊/小问题；用户主动附加的附件/选区不受影响 */
  cleanMode?: boolean;
  /** 会话模型选择（2026-08-17）：模型管理 backend 名；undefined = 按模型管理路由配置，
   *  选中时随每次发送透传后端（优先级最高） */
  chatModel?: string;
  /** 上下文断点（2026-08-17）：该消息及之前的对话不再随发送进后端（界面保留可回看）；
   *  「清理上下文」设断点为最后一条消息，「压缩上下文」设为最后一条被压缩的消息 */
  contextBreakpoint?: string;
  /** 历史压缩摘要（2026-08-17）：断点之前旧对话的 LLM 摘要，随每次发送透传后端 */
  contextSummary?: string;
}

interface ChatState {
  tabs: ChatTab[];
  activeTabId: string;
  busy: boolean;
  runId: string | null;

  // Phase 2G 业务功能点上下文（由 BiznavChatBridge 写入，headless 订阅者单向桥接）
  selectedFeatureContext: FeatureContextPayload | null;
  // Phase 2H 运营工作台上下文（由 OperationsWorkbench 写入；优先于 feature 树上下文）
  opsNavContext: FeatureContextPayload | null;

  // reqflow V1 需求对齐上下文：多功能点（功能点树「发起改造需求」写入）
  alignmentFeatures: FeatureContextPayload[] | null;

  // Phase 2D V0 业务技能上下文（由 agentStream 写入，SKILL_MATCHED 事件）
  selectedSkill: { skill_id: string; skill_name: string; matched_keywords: string[] } | null;

  // Phase 4 V0 推理模式：normal = 端侧优先，performance = 全走云端
  inferenceMode: 'normal' | 'performance';

  // 整轮耗时统计（2026-08-07）：runStartTs 发送时记；done 时算差值进 lastRunMs。
  // 不进 persist（运行态）。
  runStartTs: number | null;
  lastRunMs: number | null;

  // Phase 18 会话级自主性：interactive = 每步等人；auto = 按推荐项自动继续。
  // 不持久化（不进 persist）：重启/新会话回落 interactive，需重新开关 + 弹窗确认。
  autonomy: 'interactive' | 'auto';

  // 本轮任务改动文件累积（2026-08-19）：agentStream 收 builtin_tool_done
  // （write_file / edit_file 成功）写入，done 时汇总成 changed_files 卡片追入对话。
  // 不持久化（运行态；卡片消息本身随 tabs 持久化）。
  changedFiles: string[];

  // tab 操作
  newTab: (title?: string, mode?: WorkMode) => void;
  closeTab: (id: string) => void;
  switchTab: (id: string) => void;
  renameTab: (id: string, title: string) => void;
  /** 把 srcId 拖到 dstId 之前（或末尾如果 dstId 为 null） */
  moveTab: (srcId: string, dstId: string | null) => void;
  /**
   * 模式隔离（2026-08-11）：切到该模式的页签组；没有则新建。
   * 只区分 operator 与 full（其余模式不渲染对话，归入 full），
   * 保证运营专家团对话与开发对话互不串场。
   */
  ensureModeTab: (mode: WorkMode) => void;

  // 消息操作（始终作用于 active tab）
  append: (m: ChatMessage) => void;
  /** 追加 Codex/Claude 风格执行 step —— 同 runId+category 自动合并 running → ok */
  appendExecution: (m: ChatMessage) => void;
  update: (id: string, patch: Partial<ChatMessage>) => void;
  setBusy: (b: boolean) => void;
  setRunId: (id: string | null) => void;
  setFeatureContext: (ctx: FeatureContextPayload | null) => void;
  setOpsNavContext: (ctx: FeatureContextPayload | null) => void;
  setAlignmentFeatures: (fs: FeatureContextPayload[] | null) => void;
  setSelectedSkill: (s: { skill_id: string; skill_name: string; matched_keywords: string[] } | null) => void;

  // 2026-08-19 改动文件累积（去重）+ 清空
  addChangedFile: (path: string) => void;
  clearChangedFiles: () => void;

  // Phase 4 V0
  setInferenceMode: (mode: 'normal' | 'performance') => void;
  toggleInferenceMode: () => void;

  // Phase 18
  setAutonomy: (a: 'interactive' | 'auto') => void;

  // 2026-08-07
  setRunStartTs: (ts: number | null) => void;
  setLastRunMs: (ms: number | null) => void;
  /** 给指定 tab 绑定后端 session id（sessions 归档） */
  setTabSessionId: (tabId: string, sessionId: string) => void;
  /** 开关指定 tab 的纯净对话模式（2026-08-14，随 tabs 持久化） */
  setTabCleanMode: (tabId: string, on: boolean) => void;
  /** 设置指定 tab 的会话模型（2026-08-17）；null = 回落模型管理配置 */
  setTabChatModel: (tabId: string, modelName: string | null) => void;
  /** 上下文断点式清理（2026-08-17）：断点设到最后一条消息，
   *  界面消息保留但后续发送不再携带；同时清空已有压缩摘要 */
  clearTabContext: (tabId: string) => void;
  /** 应用压缩结果（2026-08-17）：写入新摘要 + 断点设为最后一条被压缩的消息 */
  applyTabCompression: (tabId: string, summary: string, breakpointId: string) => void;
}

const newId = (): string => `tab-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const initialTab = (): ChatTab => ({
  id: newId(),
  title: '新会话',
  messages: [],
});

// ---- 持久化裁剪与恢复清洗（2026-08-07）----------------------------------

/** localStorage 约 5MB 上限保护：只留最近 N 个 tab / 每 tab 最近 M 条消息 */
const MAX_PERSIST_TABS = 20;
const MAX_PERSIST_MESSAGES = 500;

function trimForPersist(tabs: ChatTab[]): ChatTab[] {
  return tabs.slice(-MAX_PERSIST_TABS).map((t) => ({
    ...t,
    messages: t.messages.slice(-MAX_PERSIST_MESSAGES),
  }));
}

/** 恢复时把上次关机卡住的 running 执行步骤降为 ok，避免永久转圈 */
function sanitizeRestored(tabs: ChatTab[]): ChatTab[] {
  return tabs.map((t) => ({
    ...t,
    messages: t.messages.map((m) =>
      m.kind === 'execution' && m.status === 'running' ? { ...m, status: 'ok' as const } : m,
    ),
  }));
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => {
      const t0 = initialTab();
      return {
    tabs: [t0],
    activeTabId: t0.id,
    busy: false,
    runId: null,
    selectedFeatureContext: null,
    opsNavContext: null,
    alignmentFeatures: null,
    selectedSkill: null,
    inferenceMode: 'normal',  // Phase 4 V0: 默认正常模式（端侧优先）
    autonomy: 'interactive',  // Phase 18: 默认交互模式（安全默认值）
    changedFiles: [],          // 2026-08-19: 本轮改动文件累积（运行态）
    runStartTs: null,
    lastRunMs: null,

    newTab: (title, mode) => {
      const t: ChatTab = { id: newId(), title: title ?? '新会话', messages: [], mode: mode ?? 'full' };
      set((s) => ({ tabs: [...s.tabs, t], activeTabId: t.id, runId: null }));
    },

    ensureModeTab: (mode) => {
      const target: WorkMode = mode === 'operator' ? 'operator' : 'full';
      const { tabs, activeTabId } = get();
      const own = tabs.filter((t) => (t.mode ?? 'full') === target);
      if (own.length === 0) {
        const t: ChatTab = { id: newId(), title: '新会话', messages: [], mode: target };
        set((s) => ({ tabs: [...s.tabs, t], activeTabId: t.id, runId: null }));
        return;
      }
      if (!own.some((t) => t.id === activeTabId)) {
        set({ activeTabId: own[own.length - 1].id, runId: null });
      }
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
    setOpsNavContext: (ctx) => set({ opsNavContext: ctx }),
    setAlignmentFeatures: (fs) => set({ alignmentFeatures: fs }),

    // Phase 4 V0
    setInferenceMode: (mode) => set({ inferenceMode: mode }),
    toggleInferenceMode: () =>
      set((s) => ({
        inferenceMode: s.inferenceMode === 'normal' ? 'performance' : 'normal',
      })),

    // Phase 18
    setAutonomy: (a) => set({ autonomy: a }),

    // 2026-08-19 改动文件累积（同路径去重）+ 清空
    addChangedFile: (path) =>
      set((s) =>
        s.changedFiles.includes(path) ? s : { changedFiles: [...s.changedFiles, path] },
      ),
    clearChangedFiles: () => set({ changedFiles: [] }),

    // 2026-08-07
    setRunStartTs: (ts) => set({ runStartTs: ts }),
    setLastRunMs: (ms) => set({ lastRunMs: ms }),
    setTabSessionId: (tabId, sessionId) =>
      set((s) => ({
        tabs: s.tabs.map((t) =>
          t.id === tabId ? { ...t, backendSessionId: sessionId } : t,
        ),
      })),
    setTabCleanMode: (tabId, on) =>
      set((s) => ({
        tabs: s.tabs.map((t) => (t.id === tabId ? { ...t, cleanMode: on } : t)),
      })),
    setTabChatModel: (tabId, modelName) =>
      set((s) => ({
        tabs: s.tabs.map((t): ChatTab => {
          if (t.id !== tabId) return t;
          if (modelName) return { ...t, chatModel: modelName };
          // 回落默认：剥掉字段（exactOptionalPropertyTypes 下不能显式赋 undefined）
          const { chatModel: _omit, ...rest } = t;
          return rest;
        }),
      })),
    clearTabContext: (tabId) =>
      set((s) => ({
        tabs: s.tabs.map((t): ChatTab => {
          if (t.id !== tabId || t.messages.length === 0) return t;
          const lastId = t.messages[t.messages.length - 1].id;
          // 清理同时作废旧摘要（断点前内容已全部排除，摘要失去对应对象）
          const { contextSummary: _omitSummary, ...rest } = t;
          return { ...rest, contextBreakpoint: lastId };
        }),
      })),
    applyTabCompression: (tabId, summary, breakpointId) =>
      set((s) => ({
        tabs: s.tabs.map((t) =>
          t.id === tabId
            ? { ...t, contextSummary: summary, contextBreakpoint: breakpointId }
            : t,
        ),
      })),
      };
    },
    {
      name: 'eaide-chat-v1',
      storage: createJSONStorage(() => localStorage),
      // 只持久化会话本体；busy/runId/autonomy/上下文不进（运行态重启重置）
      partialize: (s) => ({
        tabs: trimForPersist(s.tabs),
        activeTabId: s.activeTabId,
        inferenceMode: s.inferenceMode,
      }),
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<
          Pick<ChatState, 'tabs' | 'activeTabId' | 'inferenceMode'>
        >;
        let tabs =
          Array.isArray(p.tabs) && p.tabs.length > 0 ? sanitizeRestored(p.tabs) : current.tabs;
        // 保底：至少一个 tab，activeTabId 必须落在实际存在的 tab 上
        if (tabs.length === 0) tabs = [initialTab()];
        const activeTabId = tabs.some((t) => t.id === p.activeTabId)
          ? (p.activeTabId as string)
          : tabs[0].id;
        return {
          ...current,
          tabs,
          activeTabId,
          inferenceMode: p.inferenceMode ?? current.inferenceMode,
        };
      },
    },
  ),
);

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
  const opsCtx = useChatStore((s) => s.opsNavContext);
  const alignment = useChatStore((s) => s.alignmentFeatures);
  const skills = useSkillsStore((s) => s.skills);

  // reqflow V1 需求对齐模式优先：多功能点 + 需求分析角色设定
  if (alignment && alignment.length > 0) {
    const lines: string[] = ['【需求改造对齐上下文】'];
    for (const f of alignment) {
      lines.push(...renderFeatureBlock(f));
      const skill = skills.find((s) => s.id === f.skill_id);
      if (skill) lines.push(...renderSkillBlock(skill));
    }
    lines.push('');
    lines.push(
      '（当前为需求改造对齐会话。用户是不懂技术的业务人员（运营/柜员/业务主管），\n' +
      '你必须严格遵守以下沟通规则：\n' +
      '1. 全程用业务语言，禁止出现任何技术术语：不得提 表名/字段类型/VARCHAR/DDL/Entity/Mapper/DTO/驼峰命名/索引/接口路径 等，\n' +
      '   也不得反问用户“哪个类/哪个表/什么数据类型”这类只有开发才能回答的问题；\n' +
      '2. 需要补充信息时，只问业务问题：新字段做什么用、展示在哪里、谁来填/谁能看、是否必填、\n' +
      '   存量记录怎么办、是否影响现有业务流程等；\n' +
      '3. 可行性与影响分析用大白话表达：说“会影响到 XX 页面的展示和 XX 流程”，\n' +
      '   不要说“需要改数据库脚本和实体类”；实现难度用“小改动/中等/较大改造”表达；\n' +
      '4. 实现方案只作为给开发人员的附注简要提及（一两句通俗描述即可），\n' +
      '   不展开技术细节，不要求用户提供技术方案，你只输出文字不实际改代码；\n' +
      '5. 对齐充分后用户会点击「生成需求卡片」，卡片会把结论转交给审批和开发人员。\n' +
      '你的职责：帮用户把需求说清楚 → 用业务语言分析可行性、对其他功能的影响、涉及的外部系统。）'
    );
    return lines.join('\n');
  }

  // Phase 2H：运营工作台选中业务 > 功能点树选中 > 无上下文
  const activeCtx = opsCtx ?? ctx;
  if (!activeCtx) return '';

  const lines: string[] = ['【当前业务功能点上下文】'];
  lines.push(...renderFeatureBlock(activeCtx));
  const skill = skills.find((s) => s.id === activeCtx.skill_id);
  if (skill) lines.push(...renderSkillBlock(skill));
  lines.push('');
  lines.push('（以上为已导入工程中选定的业务功能点；项目、功能、关联代码均已明确，直接基于这些信息回答，不要再反问功能名称、项目位置、技术栈等已知信息。如用户提出与该功能无关的问题，可正常脱离上下文。）');
  return lines.join('\n');
}

/** 绑定 Skill 经验注入：业务流程 / 材料清单 / 风险控制 / 数据字典引用规则 */
export function renderSkillBlock(skill: Skill): string[] {
  const lines: string[] = [];
  lines.push('');
  lines.push(`【绑定 Skill 经验：${skill.name}（${skill.id}）】`);
  if (skill.system_prompt) {
    lines.push(skill.system_prompt);
  }
  if (skill.few_shot_examples.length > 0) {
    lines.push('参考示例：');
    for (const ex of skill.few_shot_examples.slice(0, 5)) {
      lines.push(`[${ex.role}] ${ex.content.slice(0, 600)}`);
    }
  }
  // 专家团预设（本业务默认专家团 / 办理材料 / 交付物，供模型判断与执行）
  if ((skill.required_expert_team_ids ?? []).length > 0) {
    lines.push(`默认专家团：${skill.required_expert_team_ids.join('、')}`);
  }
  if ((skill.materials ?? []).length > 0) {
    lines.push(`办理材料清单：${skill.materials.join('、')}`);
  }
  if ((skill.deliverables ?? []).length > 0) {
    lines.push(`最终交付物：${skill.deliverables.join('、')}`);
  }
  lines.push('');
  lines.push(
    '（当前会话已自动加载该功能点绑定的 Skill。请严格按 Skill 中的业务流程、' +
    '材料清单与风险控制执行；Skill 中标注的公共参数请到「数据字典」按 key 查询，' +
    '不要臆造参数值；涉及外部系统（如 OCR）时按占位说明提示人工接入。）'
  );
  return lines;
}

// ---------------------------------------------------------------------------
// 专家团上下文注入（运营工作台自动/手动选择后，发送时拼接）
// ---------------------------------------------------------------------------

/** 纯函数版（便于测试）：把选中的专家团拼成上下文字符串 */
export function buildExpertTeamSnippet(ids: string[], teams: ExpertTeam[]): string {
  if (ids.length === 0) return '';
  const lines: string[] = [];
  for (const id of ids) {
    const t = teams.find((x) => x.id === id);
    if (t) lines.push(...renderExpertTeamBlock(t));
  }
  return lines.join('\n');
}

/**
 * 专家团上下文片段：ChatInput 发送时拼到 prompt 前缀。
 * 选中团为空 → 返回 '' 不注入。与 useFeatureContextPromptSnippet 同机制。
 */
export function useExpertTeamPromptSnippet(): string {
  const ids = useExpertTeamStore((s) => s.selectedTeamIds);
  const teams = useExpertTeamStore((s) => s.teams);
  return buildExpertTeamSnippet(ids, teams);
}

/** 单个专家团渲染：团定位 + 逐成员（角色/职责/关注点/输出/Prompt）+ 协作规则 */
export function renderExpertTeamBlock(team: ExpertTeam): string[] {
  const lines: string[] = [];
  lines.push('');
  lines.push(`【专家团上下文：${team.name}（${team.id}）】`);
  if (team.description) lines.push(team.description);
  if (team.applicable_scenarios.length > 0) {
    lines.push(`适用场景：${team.applicable_scenarios.join('、')}`);
  }
  lines.push('本专家团成员：');
  for (const m of team.members) {
    lines.push(`■ ${m.name}（${m.role}）`);
    if (m.responsibilities.length > 0) {
      lines.push(`  职责：${m.responsibilities.join('；')}`);
    }
    if (m.focus_points.length > 0) {
      lines.push(`  关注点：${m.focus_points.join('；')}`);
    }
    if (m.outputs.length > 0) {
      lines.push(`  输出：${m.outputs.join('、')}`);
    }
    if (m.prompt) lines.push(`  角色指令：${m.prompt}`);
  }
  lines.push('');
  lines.push(
    '（你将以该专家团身份协同工作：项目经理统筹任务与资料判断，各专家按职责' +
    '输出结构化意见，报告主笔汇总成稿，质量控制复核；资料不足不得给出确定性' +
    '结论，最终判断由人工负责。）'
  );
  return lines;
}

// ---------------------------------------------------------------------------
// 上下文大小估算与断点过滤（2026-08-17）—— 纯函数，供 ChatInput / 测试复用
// ---------------------------------------------------------------------------

/** 粗估 token（~4 字符/token，与后端 context_trim / PrivateLLM 估算口径一致） */
export function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

/** 取断点之后、会随下次发送的会话消息（user/assistant 且有内容） */
export function tabContextMessages(tab: ChatTab): ChatMessage[] {
  const bp = tab.contextBreakpoint;
  const msgs = tab.messages;
  let start = 0;
  if (bp) {
    const idx = msgs.findIndex((m) => m.id === bp);
    if (idx >= 0) start = idx + 1;
  }
  return msgs
    .slice(start)
    .filter((m) => (m.role === 'user' || m.role === 'assistant') && Boolean(m.content));
}

/** 将随下次发送的会话 history 估算 token（不含项目上下文/附件/输入文本） */
export function estimateHistoryTokens(tab: ChatTab): number {
  return tabContextMessages(tab).reduce((sum, m) => sum + estimateTokens(m.content), 0);
}

/** 单个功能点上下文渲染（首行含功能名，其余缩进子项） */
function renderFeatureBlock(ctx: FeatureContextPayload): string[] {
  const lines: string[] = [];
  lines.push(`- 功能：${ctx.feature_name} (${ctx.feature_id})`);
  if (ctx.feature_description) {
    lines.push(`  - 描述：${ctx.feature_description}`);
  }
  if (ctx.related_files.length > 0) {
    lines.push('  - 关联文件：');
    for (const f of ctx.related_files.slice(0, 10)) {
      lines.push(`    - ${f.path}`);
    }
    if (ctx.related_files.length > 10) {
      lines.push(`    - …（还有 ${ctx.related_files.length - 10} 个）`);
    }
  }
  if (ctx.related_apis.length > 0) {
    lines.push('  - 关联 API：');
    for (const a of ctx.related_apis.slice(0, 5)) {
      lines.push(`    - ${a.method} ${a.path}`);
    }
  }
  if (ctx.related_tables.length > 0) {
    lines.push('  - 关联表：');
    for (const t of ctx.related_tables.slice(0, 5)) {
      lines.push(`    - ${t.name}`);
    }
  }
  if (ctx.business_rules.length > 0) {
    lines.push('  - 业务规则：');
    for (const r of ctx.business_rules.slice(0, 10)) {
      lines.push(`    - ${r}`);
    }
  }
  return lines;
}
