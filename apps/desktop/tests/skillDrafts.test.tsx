/**
 * Phase 19 V1 自进化回归 —— 技能页「待审草稿」面板（SkillDraftsPanel）。
 *
 * 覆盖：
 *   - 加载并展示待审草稿（名称 + slug）
 *   - 采纳 → evolutionSkillDraftApprove + 技能列表刷新（loadSkills）
 *   - 拒绝 → evolutionSkillDraftReject
 *   - 空态占位文案
 *   - skill_draft_ready SSE 事件触发刷新
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/react';

const evolutionSkillDrafts = vi.fn();
const evolutionSkillDraftApprove = vi.fn();
const evolutionSkillDraftReject = vi.fn();
const skillsList = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    evolutionSkillDrafts: (...args: unknown[]) => evolutionSkillDrafts(...args),
    evolutionSkillDraftApprove: (...args: unknown[]) => evolutionSkillDraftApprove(...args),
    evolutionSkillDraftReject: (...args: unknown[]) => evolutionSkillDraftReject(...args),
    skillsList: (...args: unknown[]) => skillsList(...args),
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

import { SkillDraftsPanel } from '@/components/skills/SkillDraftsPanel';

function seed(drafts: unknown[]): void {
  evolutionSkillDrafts.mockClear();
  evolutionSkillDraftApprove.mockClear();
  evolutionSkillDraftReject.mockClear();
  skillsList.mockClear();
  evolutionSkillDrafts.mockResolvedValue({ ok: true, items: drafts });
  evolutionSkillDraftApprove.mockResolvedValue({
    ok: true,
    id: 1,
    skill_id: 'daily_report_check',
    path: 'D:/skills/daily_report_check.yaml',
  });
  evolutionSkillDraftReject.mockResolvedValue({ ok: true, id: 1, status: 'rejected' });
  skillsList.mockResolvedValue({ skills: [] });
}

const DRAFT = {
  id: 5,
  slug: 'daily_report_check',
  name: '日报核对规范',
  yaml_text: 'schema_version: "1.0"\nid: daily_report_check\nname: 日报核对规范',
  task_signature: 'abcdef1234567890',
  status: 'draft',
  ts: '2026-08-31T00:00:00',
};

describe('SkillDraftsPanel（Phase 19 V1）', () => {
  it('展示待审草稿列表', async () => {
    seed([DRAFT]);
    const { getByText } = render(<SkillDraftsPanel />);
    await waitFor(() => expect(getByText(/日报核对规范/)).toBeTruthy());
    expect(getByText(/daily_report_check/)).toBeTruthy();
  });

  it('空库时展示占位文案', async () => {
    seed([]);
    const { getByText } = render(<SkillDraftsPanel />);
    await waitFor(() =>
      expect(getByText(/暂无待审草稿。同类任务多次成功后/)).toBeTruthy(),
    );
  });

  it('采纳草稿：调用 approve 并刷新技能列表', async () => {
    seed([DRAFT]);
    // confirm() 默认 jsdom 未实现 → 打桩返回 true
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { getByText } = render(<SkillDraftsPanel />);
    await waitFor(() => expect(getByText(/日报核对规范/)).toBeTruthy());
    fireEvent.click(getByText('采纳'));
    await waitFor(() => expect(evolutionSkillDraftApprove).toHaveBeenCalledWith(5));
    await waitFor(() => expect(skillsList).toHaveBeenCalled()); // 技能列表刷新
  });

  it('拒绝草稿：调用 reject', async () => {
    seed([DRAFT]);
    const { getByText } = render(<SkillDraftsPanel />);
    await waitFor(() => expect(getByText(/日报核对规范/)).toBeTruthy());
    fireEvent.click(getByText('拒绝'));
    await waitFor(() => expect(evolutionSkillDraftReject).toHaveBeenCalledWith(5));
  });

  it('skill_draft_ready 事件触发列表刷新', async () => {
    seed([]);
    render(<SkillDraftsPanel />);
    await waitFor(() => expect(evolutionSkillDrafts).toHaveBeenCalledTimes(1));
    const handler = listeners.get('agent://skill_draft_ready');
    expect(handler).toBeTruthy();
    handler?.({ payload: { draft_id: 9 } });
    await waitFor(() => expect(evolutionSkillDrafts).toHaveBeenCalledTimes(2));
  });
});
