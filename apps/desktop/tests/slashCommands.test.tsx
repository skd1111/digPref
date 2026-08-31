/**
 * `/` 系统指令（V1：Skill）测试（2026-08-28）。
 *
 * 交互契约：输入框键入 `/` 浮出已启用 Skill 列表（鼠标悬浮/点击与
 * ↑↓ + Enter/Tab + Esc 键盘双通道）；选中后挂上下文 chip，发送时把
 * skill 经验块拼进 prompt 并透传 lastSkillId；发送后一次性清空。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, act } from '@testing-library/react';
import type { Skill } from '@/types/skill';

const invokeMock = vi.hoisted(() => vi.fn().mockResolvedValue('run-test'));

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    routerListBackends: vi.fn().mockResolvedValue({ backends: [] }),
    routerGetGenLimits: vi
      .fn()
      .mockResolvedValue({ ok: true, limits: { max_output_tokens: 4096, default_context_window: 4096 } }),
    chatAttachFile: vi.fn(),
    cancel: vi.fn(),
    sessionsCreate: vi.fn().mockResolvedValue({ id: 'sess-test' }),
    sessionsAppendMessage: vi.fn().mockResolvedValue(undefined),
    biznavProfile: vi.fn().mockResolvedValue({ has_profile: false, profile: '' }),
    skillsList: vi.fn().mockResolvedValue({ skills: [] }),
  },
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

import { ChatInput } from '@/components/chat/ChatInput';
import { parseSlashQuery, filterSkillsForSlash, SLASH_MAX_ITEMS } from '@/lib/slashCommands';
import { useChatStore } from '@/store/chatStore';
import { useSkillsStore } from '@/store/skillsStore';

function makeSkill(partial: Partial<Skill> & { id: string; name: string }): Skill {
  return {
    schema_version: '1.0',
    description: '',
    version: '1.0',
    author: '',
    tags: [],
    risk_level: 'low',
    enabled: true,
    trigger_keywords: [],
    mcp_servers: [],
    allowed_tools: [],
    role: 'utility',
    system_prompt: '',
    few_shot_examples: [],
    required_expert_team_ids: [],
    materials: [],
    deliverables: [],
    source_path: '',
    loaded_at: Date.now(),
    validation_errors: [],
    ...partial,
  };
}

const PPT_SKILL = makeSkill({
  id: 'office_pptx_designer',
  name: 'PPT 设计规范',
  description: '商务风演示稿生成规范',
  trigger_keywords: ['ppt', '演示'],
  system_prompt: '你是一名资深演示设计师，遵循金字塔原理。',
});
const DOC_SKILL = makeSkill({
  id: 'office_doc_writer',
  name: 'Word 成文规范',
  description: '正式公文与报告写作',
  system_prompt: '公文写作必须严谨正式，遵循行文规范。',
});
const DISABLED_SKILL = makeSkill({
  id: 'legacy_disabled',
  name: '已停用技能',
  enabled: false,
});

beforeEach(() => {
  localStorage.clear();
  invokeMock.mockClear();
  useSkillsStore.setState({ skills: [PPT_SKILL, DOC_SKILL, DISABLED_SKILL] });
  useChatStore.setState((s) => ({
    tabs: [{ id: 'tab-a', title: '新会话', messages: [] }],
    activeTabId: 'tab-a',
    busy: false,
    runId: null,
    lastSkillId: null,
    busyTabIds: [],
    runTabMap: {},
    tabRunIds: {},
    inferenceMode: s.inferenceMode,
  }));
});

describe('parseSlashQuery 纯函数', () => {
  it('/ 开头单行文本解析出查询词', () => {
    expect(parseSlashQuery('/')).toBe('');
    expect(parseSlashQuery('/ppt')).toBe('ppt');
    expect(parseSlashQuery('/ppt 帮我做')).toBe('ppt');
  });

  it('非指令文本返 null', () => {
    expect(parseSlashQuery('')).toBeNull();
    expect(parseSlashQuery('你好')).toBeNull();
    expect(parseSlashQuery('路径 C:/a')).toBeNull();
    expect(parseSlashQuery('/a\nb')).toBeNull(); // 多行视为普通提问
  });
});

describe('filterSkillsForSlash 过滤', () => {
  it('只列启用项；空查询返全部启用', () => {
    const out = filterSkillsForSlash([PPT_SKILL, DOC_SKILL, DISABLED_SKILL], '');
    expect(out.map((s) => s.id)).toEqual(['office_pptx_designer', 'office_doc_writer']);
  });

  it('按 id/名称/关键词/描述匹配（不区分大小写）', () => {
    expect(filterSkillsForSlash([PPT_SKILL, DOC_SKILL], 'PPT').map((s) => s.id)).toEqual([
      'office_pptx_designer',
    ]);
    expect(filterSkillsForSlash([PPT_SKILL, DOC_SKILL], 'word').map((s) => s.id)).toEqual([
      'office_doc_writer',
    ]);
    expect(filterSkillsForSlash([PPT_SKILL, DOC_SKILL], '演示').map((s) => s.id)).toEqual([
      'office_pptx_designer',
    ]);
    expect(filterSkillsForSlash([PPT_SKILL, DOC_SKILL], '不存在')).toEqual([]);
  });

  it('结果截断到上限', () => {
    const many = Array.from({ length: SLASH_MAX_ITEMS + 5 }, (_, i) =>
      makeSkill({ id: `s${i}`, name: `技能${i}` }),
    );
    expect(filterSkillsForSlash(many, '').length).toBe(SLASH_MAX_ITEMS);
  });
});

describe('ChatInput `/` 菜单交互', () => {
  it('键入 / 浮出菜单：只含启用项，禁用项不出现', () => {
    const { container } = render(<ChatInput />);
    const ta = container.querySelector('textarea')!;
    fireEvent.change(ta, { target: { value: '/' } });
    expect(screen.getByText('系统指令 · 业务技能（Skill）')).toBeTruthy();
    expect(screen.getByText('PPT 设计规范')).toBeTruthy();
    expect(screen.getByText('Word 成文规范')).toBeTruthy();
    expect(screen.queryByText('已停用技能')).toBeNull();
  });

  it('查询词过滤列表', () => {
    const { container } = render(<ChatInput />);
    const ta = container.querySelector('textarea')!;
    fireEvent.change(ta, { target: { value: '/ppt' } });
    expect(screen.getByText('PPT 设计规范')).toBeTruthy();
    expect(screen.queryByText('Word 成文规范')).toBeNull();
  });

  it('鼠标点击选中 → 挂 🧩 chip、剥离指令词、菜单关闭', () => {
    const { container } = render(<ChatInput />);
    const ta = container.querySelector('textarea')!;
    fireEvent.change(ta, { target: { value: '/ppt 做一份介绍' } });
    fireEvent.click(screen.getByText('PPT 设计规范'));
    // 菜单关闭 + 指令词剥离（保留其余文本）
    expect(screen.queryByText('系统指令 · 业务技能（Skill）')).toBeNull();
    expect((ta as HTMLTextAreaElement).value).toBe('做一份介绍');
    // 待注入 chip 可见
    expect(screen.getByText(/🧩 Skill：PPT 设计规范/)).toBeTruthy();
  });

  it('键盘 ↑↓ 循环移高亮，Enter 选中，Esc 关闭', () => {
    const { container } = render(<ChatInput />);
    const ta = container.querySelector('textarea')!;
    fireEvent.change(ta, { target: { value: '/' } });
    // ↓ 移到第二项（Word），再 ↓ 循环回第一项（PPT）
    fireEvent.keyDown(ta, { key: 'ArrowDown' });
    fireEvent.keyDown(ta, { key: 'ArrowDown' });
    fireEvent.keyDown(ta, { key: 'Enter' });
    expect(screen.getByText(/🧩 Skill：PPT 设计规范/)).toBeTruthy();
    // Enter 用于选中而非发送：未触发 agent_chat
    expect(invokeMock).not.toHaveBeenCalled();

    // Esc 关闭菜单（再键入同查询词不重开；换查询词重开）
    fireEvent.change(ta, { target: { value: '/' } });
    fireEvent.keyDown(ta, { key: 'Escape' });
    expect(screen.queryByText('系统指令 · 业务技能（Skill）')).toBeNull();
  });

  it('发送时 skill 经验块拼进 prompt 且透传 lastSkillId，发送后一次性清空', async () => {
    const { container } = render(<ChatInput />);
    const ta = container.querySelector('textarea')!;
    fireEvent.change(ta, { target: { value: '/ppt' } });
    fireEvent.click(screen.getByText('PPT 设计规范'));
    await act(async () => {
      fireEvent.change(ta, { target: { value: '做一份 10 页介绍' } });
    });
    const sendBtn = Array.from(container.querySelectorAll('button')).find((b) => b.title === '发送')!;
    await act(async () => {
      sendBtn.click();
    });

    const calls = invokeMock.mock.calls.filter((c) => c[0] === 'agent_chat');
    expect(calls.length).toBe(1);
    const args = calls[0][1] as { prompt: string; lastSkillId: string | null };
    expect(args.prompt).toContain('【已加载 Skill（用户经 / 指令主动选择）· PPT 设计规范】');
    expect(args.prompt).toContain('你是一名资深演示设计师，遵循金字塔原理。');
    expect(args.prompt).toContain('【用户问题】\n做一份 10 页介绍');
    expect(args.lastSkillId).toBe('office_pptx_designer');
    // 一次性注入：发送后待选态清空（chip 消失）
    expect(screen.queryByText(/🧩 Skill：/)).toBeNull();
  });

  it('`/` 强钉互斥：剔除功能点绑定技能段，功能点上下文保留，透传 pinnedSkillId', async () => {
    // 功能点绑定了 Word 技能（自动注入路径），用户用 `/` 钉住 PPT 技能 → 只留钉住的
    useChatStore.getState().setFeatureContext({
      feature_id: 'f1',
      feature_name: '功能点A',
      feature_description: '测试功能点',
      skill_id: 'office_doc_writer',
      related_files: [],
      related_apis: [],
      related_tables: [],
      business_rules: [],
      source: 'manual',
    });
    try {
      const { container } = render(<ChatInput />);
      const ta = container.querySelector('textarea')!;
      fireEvent.change(ta, { target: { value: '/ppt' } });
      fireEvent.click(screen.getByText('PPT 设计规范'));
      await act(async () => {
        fireEvent.change(ta, { target: { value: '做一份介绍' } });
      });
      const sendBtn = Array.from(container.querySelectorAll('button')).find((b) => b.title === '发送')!;
      await act(async () => {
        sendBtn.click();
      });

      const calls = invokeMock.mock.calls.filter((c) => c[0] === 'agent_chat');
      expect(calls.length).toBe(1);
      const args = calls[0][1] as { prompt: string; pinnedSkillId: string | null };
      // 钉住的技能段在；绑定的技能段被物理剔除（不进入模型输入空间）
      expect(args.prompt).toContain('你是一名资深演示设计师，遵循金字塔原理。');
      expect(args.prompt).not.toContain('公文写作必须严谨正式');
      // 功能点上下文本身保留（只剔技能段）
      expect(args.prompt).toContain('功能点A');
      // 强钉字段透传 → 后端短路路由器，关键词命中也被排除
      expect(args.pinnedSkillId).toBe('office_pptx_designer');
    } finally {
      useChatStore.getState().setFeatureContext(null);
    }
  });
});
