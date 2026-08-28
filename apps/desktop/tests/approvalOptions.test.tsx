/**
 * Phase 14/18 ApprovalCard：推荐选项渲染（向后兼容二元审批）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { ApprovalRequest } from '@eaide/shared-protocol';

vi.mock('@/ipc/invoke', () => ({ invoke: vi.fn() }));

import { ApprovalCard } from '@/components/chat/ApprovalCard';
import { useChatStore } from '@/store/chatStore';

const baseApproval: ApprovalRequest = {
  id: 'ap-1',
  runId: 'run-1',
  plan: { server: 'database', name: 'run_sql', args: {} },
  riskLevel: 'medium',
  createdAt: new Date().toISOString(),
};

describe('ApprovalCard options (Phase 18)', () => {
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

  async function render(approval: ApprovalRequest): Promise<void> {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<ApprovalCard approval={approval} />);
    });
  }

  it('有 options 时渲染候选列表且推荐项高亮 + 推荐理由', async () => {
    await render({
      ...baseApproval,
      options: [
        { id: 'o1', label: '执行（限近 7 天）', adjustedPlan: 'SELECT ...', riskNote: null },
        { id: 'o2', label: '不执行', adjustedPlan: '', riskNote: '取消本次操作' },
      ],
      recommendedOptionId: 'o1',
      recommendationReason: '限定时间窗后风险可控',
    });
    const text = container.textContent ?? '';
    expect(text).toContain('候选方案');
    expect(text).toContain('⭐ 执行（限近 7 天）');
    expect(text).toContain('不执行');
    expect(text).toContain('推荐理由：限定时间窗后风险可控');
    // Approve / Reject 按钮仍在
    expect(text).toContain('Approve');
    expect(text).toContain('Reject');
  });

  it('无 options 时保持二元审批（向后兼容）', async () => {
    await render(baseApproval);
    const text = container.textContent ?? '';
    expect(text).not.toContain('候选方案');
    expect(text).toContain('Approve');
    expect(text).toContain('Reject');
  });

  it('卡片不展示原始调用参数，只留操作概要（2026-08-25）', async () => {
    await render({
      ...baseApproval,
      plan: { server: 'builtin', name: 'shell', args: { command: 'DROP TABLE users' } },
    });
    const text = container.textContent ?? '';
    // 概要：工具名可见；原始参数（含敏感 SQL）绝不落卡面，留痕在思维链/审计
    expect(text).toContain('builtin · shell');
    expect(text).toContain('执行参数详见右侧执行过程与审计记录');
    expect(text).not.toContain('DROP TABLE users');
    expect(text).not.toContain('计划：');
  });

  it('非双重确认风险级提供「此后都按此执行」，high 不提供（2026-08-25）', async () => {
    await render(baseApproval); // medium → 有第三按钮
    expect(container.textContent ?? '').toContain('此后都按此执行');
    await act(async () => {
      root.unmount();
    });
    container.remove();

    await render({ ...baseApproval, riskLevel: 'high' }); // 双重确认级 → 无长期豁免入口
    expect(container.textContent ?? '').not.toContain('此后都按此执行');
  });
});

describe('chatStore.resolvePendingApproval（2026-08-25）', () => {
  it('决策成功后剥离审批区并改写文案，不再卡「提交中」', () => {
    useChatStore.setState({
      tabs: [
        {
          id: 'tab-1',
          title: 't',
          messages: [
            {
              id: 'ap-1',
              role: 'system',
              content: '等待审批中...',
              pendingApproval: baseApproval,
            },
          ],
        },
      ],
      activeTabId: 'tab-1',
    });
    useChatStore.getState().resolvePendingApproval('ap-1', '✅ 已批准，正在继续执行…');
    const msg = useChatStore.getState().tabs[0].messages[0];
    expect(msg.pendingApproval).toBeUndefined();
    expect(msg.content).toBe('✅ 已批准，正在继续执行…');
  });
});
