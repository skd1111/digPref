/**
 * Phase 19 V0 自进化闭环前端回归 —— 设置页「经验库」面板（EvolutionPanel）。
 *
 * 覆盖：
 *   - 加载并展示经验（含归因 / 命中次数 / 标签）
 *   - 空库占位文案
 *   - 启停切换 / 删除（人工干预）→ 调用对应 ipc 并刷新
 *   - evolution_insight_created SSE 事件触发自动刷新
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/react';

const evolutionExperiences = vi.fn();
const evolutionExperienceToggle = vi.fn();
const evolutionExperienceDelete = vi.fn();
const evolutionStats = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    evolutionExperiences: (...args: unknown[]) => evolutionExperiences(...args),
    evolutionExperienceToggle: (...args: unknown[]) => evolutionExperienceToggle(...args),
    evolutionExperienceDelete: (...args: unknown[]) => evolutionExperienceDelete(...args),
    evolutionStats: (...args: unknown[]) => evolutionStats(...args),
    // 内嵌 PromptOptPanel（V1.5）依赖的方法：返回空列表，不干扰面板用例
    evolutionPromptVersions: () => Promise.resolve({ ok: true, items: [] }),
    evolutionPromptOptRun: () => Promise.resolve({ ok: true }),
    evolutionPromptVersionApply: () => Promise.resolve({ ok: true }),
    evolutionPromptVersionRollback: () => Promise.resolve({ ok: true }),
    skillsList: () => Promise.resolve({ skills: [] }),
  },
}));

const listeners = new Map<string, (e: { payload: unknown }) => void>();

vi.mock('@tauri-apps/api/event', () => ({
  listen: async (event: string, handler: (e: { payload: unknown }) => void) => {
    listeners.set(event, handler);
    return () => {
      listeners.delete(event);
    };
  },
}));

import { EvolutionPanel } from '@/views/settings/EvolutionPanel';

function seedItems(items: unknown[]): void {
  evolutionExperiences.mockClear();
  evolutionExperienceToggle.mockClear();
  evolutionExperienceDelete.mockClear();
  evolutionStats.mockClear();
  evolutionExperiences.mockResolvedValue({ ok: true, items });
  evolutionExperienceToggle.mockResolvedValue({ ok: true, id: 1, status: 'disabled' });
  evolutionExperienceDelete.mockResolvedValue({ ok: true, id: 1 });
  evolutionStats.mockResolvedValue({
    ok: true,
    signals_total: 12,
    user_signals: 3,
    user_up: 2,
    env_fail: 4,
    judge_avg: 4.2,
    experiences_active: 5,
    drafts_pending: 1,
  });
}

describe('EvolutionPanel（Phase 19 V0）', () => {
  it('展示经验列表（含归因与命中次数）', async () => {
    seedItems([
      {
        id: 1,
        insight: '数据查询必须先确认日期范围',
        tags: ['日期'],
        applies_to: 'data_query',
        attribution: 'reasoning',
        hit_count: 2,
        score: 0.5,
        status: 'active',
        ts: '2026-08-31T00:00:00',
      },
    ]);
    const { getByText } = render(<EvolutionPanel />);
    await waitFor(() => expect(getByText('数据查询必须先确认日期范围')).toBeTruthy());
    expect(getByText('归因：推理错误')).toBeTruthy();
    expect(getByText('命中 2 次')).toBeTruthy();
  });

  it('空库时展示占位文案', async () => {
    seedItems([]);
    const { getByText } = render(<EvolutionPanel />);
    await waitFor(() =>
      expect(getByText(/暂无经验。智能体从失败与反馈中学习/)).toBeTruthy(),
    );
  });

  it('停用与删除经验走对应 ipc 并刷新', async () => {
    seedItems([
      {
        id: 7,
        insight: '待操作的经验',
        tags: [],
        applies_to: '',
        attribution: 'tool',
        hit_count: 0,
        score: 0.5,
        status: 'active',
        ts: '2026-08-31T00:00:00',
      },
    ]);
    const { getByText } = render(<EvolutionPanel />);
    await waitFor(() => expect(getByText('待操作的经验')).toBeTruthy());

    fireEvent.click(getByText('停用'));
    await waitFor(() => expect(evolutionExperienceToggle).toHaveBeenCalledWith(7));

    fireEvent.click(getByText('删除'));
    await waitFor(() => expect(evolutionExperienceDelete).toHaveBeenCalledWith(7));
  });

  it('新经验产出事件触发列表刷新', async () => {
    seedItems([]);
    render(<EvolutionPanel />);
    await waitFor(() => expect(evolutionExperiences).toHaveBeenCalledTimes(1));
    const handler = listeners.get('agent://evolution_insight_created');
    expect(handler).toBeTruthy();
    handler?.({ payload: { experience_id: 9 } });
    await waitFor(() => expect(evolutionExperiences).toHaveBeenCalledTimes(2));
  });

  it('展示进化看板统计卡片（V1）', async () => {
    seedItems([]);
    const { getByText } = render(<EvolutionPanel />);
    await waitFor(() => expect(getByText('评测信号')).toBeTruthy());
    expect(getByText('12')).toBeTruthy(); // signals_total
    expect(getByText('2 👍 / 1 👎')).toBeTruthy();
    expect(getByText('4.20')).toBeTruthy(); // judge_avg
  });

  it('统计接口失败时静默降级（列表照常展示）', async () => {
    seedItems([
      {
        id: 1,
        insight: '经验仍可见',
        tags: [],
        applies_to: '',
        attribution: 'env',
        hit_count: 0,
        score: 0.5,
        status: 'active',
        ts: '2026-08-31T00:00:00',
      },
    ]);
    evolutionStats.mockRejectedValue(new Error('agent offline'));
    const { getByText, queryByText } = render(<EvolutionPanel />);
    await waitFor(() => expect(getByText('经验仍可见')).toBeTruthy());
    expect(queryByText('评测信号')).toBeNull();
  });
});
