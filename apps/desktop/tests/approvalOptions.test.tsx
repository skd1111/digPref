/**
 * Phase 14/18 ApprovalCard：推荐选项渲染（向后兼容二元审批）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { ApprovalRequest } from '@eaide/shared-protocol';

vi.mock('@/ipc/invoke', () => ({ invoke: vi.fn() }));

import { ApprovalCard } from '@/components/chat/ApprovalCard';

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
});
