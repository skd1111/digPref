/**
 * Phase 19 V1.5 自进化回归 —— Few-shot 影子优化实验面板（PromptOptPanel）。
 *
 * 覆盖：
 *   - 技能下拉 + 运行实验 → evolutionPromptOptRun 收到 skillId，展示增益结果
 *   - 显著增益文案 / 版本列表渲染（待采纳 → 采纳按钮）
 *   - 采纳后调用 apply 并刷新；生效版本展示回滚按钮
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/react';

const evolutionPromptOptRun = vi.fn();
const evolutionPromptVersions = vi.fn();
const evolutionPromptVersionApply = vi.fn();
const evolutionPromptVersionRollback = vi.fn();
const skillsList = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    evolutionPromptOptRun: (...args: unknown[]) => evolutionPromptOptRun(...args),
    evolutionPromptVersions: (...args: unknown[]) => evolutionPromptVersions(...args),
    evolutionPromptVersionApply: (...args: unknown[]) => evolutionPromptVersionApply(...args),
    evolutionPromptVersionRollback: (...args: unknown[]) => evolutionPromptVersionRollback(...args),
    skillsList: (...args: unknown[]) => skillsList(...args),
  },
}));

vi.mock('@tauri-apps/api/event', () => ({
  listen: async () => () => undefined,
}));

import { PromptOptPanel } from '@/components/evolution/PromptOptPanel';
import { useSkillsStore } from '@/store/skillsStore';

function seed(): void {
  evolutionPromptOptRun.mockClear();
  evolutionPromptVersions.mockClear();
  evolutionPromptVersionApply.mockClear();
  evolutionPromptVersionRollback.mockClear();
  skillsList.mockClear();
  evolutionPromptVersions.mockResolvedValue({ ok: true, items: [] });
  evolutionPromptVersionApply.mockResolvedValue({
    ok: true,
    id: 1,
    skill_id: 'daily_report_check',
    status: 'active',
  });
  evolutionPromptVersionRollback.mockResolvedValue({
    ok: true,
    id: 1,
    skill_id: 'daily_report_check',
    rolled_back_to: 0,
  });
  skillsList.mockResolvedValue({ skills: [] });
  useSkillsStore.setState({
    skills: [
      {
        schema_version: '1.0',
        id: 'daily_report_check',
        name: '日报核对规范',
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
        loaded_at: 0,
        validation_errors: [],
      },
    ],
  });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
}

describe('PromptOptPanel（Phase 19 V1.5）', () => {
  it('选择技能并运行实验，展示显著增益结果', async () => {
    seed();
    evolutionPromptOptRun.mockResolvedValue({
      ok: true,
      skill_id: 'daily_report_check',
      old_avg: 3.0,
      new_avg: 5.0,
      gain: 2.0,
      significant: true,
      version_id: 7,
      auto_adopted: false,
    });
    const { getByText, getByDisplayValue } = render(<PromptOptPanel />);
    fireEvent.change(getByDisplayValue('选择技能…'), {
      target: { value: 'daily_report_check' },
    });
    fireEvent.click(getByText('运行实验'));
    await waitFor(() =>
      expect(evolutionPromptOptRun).toHaveBeenCalledWith({ skillId: 'daily_report_check' }),
    );
    await waitFor(() =>
      expect(getByText(/显著提升，候选版本已生成/)).toBeTruthy(),
    );
  });

  it('提升不显著时展示未产出版本提示', async () => {
    seed();
    evolutionPromptOptRun.mockResolvedValue({
      ok: true,
      skill_id: 'daily_report_check',
      old_avg: 3.0,
      new_avg: 3.2,
      gain: 0.2,
      significant: false,
      version_id: null,
      auto_adopted: false,
    });
    const { getByText, getByDisplayValue } = render(<PromptOptPanel />);
    fireEvent.change(getByDisplayValue('选择技能…'), {
      target: { value: 'daily_report_check' },
    });
    fireEvent.click(getByText('运行实验'));
    await waitFor(() => expect(getByText(/提升不显著，未产出候选版本/)).toBeTruthy());
  });

  it('候选版本可采纳，采纳后刷新技能列表', async () => {
    seed();
    evolutionPromptVersions.mockResolvedValue({
      ok: true,
      items: [
        {
          id: 9,
          skill_id: 'daily_report_check',
          version: 1,
          few_shot: [{ role: 'user', content: 'x' }],
          gain: 2.0,
          status: 'candidate',
          ts: '2026-08-31T00:00:00',
        },
      ],
    });
    const { getByText } = render(<PromptOptPanel />);
    await waitFor(() => expect(getByText(/待采纳/)).toBeTruthy());
    fireEvent.click(getByText('采纳'));
    await waitFor(() => expect(evolutionPromptVersionApply).toHaveBeenCalledWith(9));
    await waitFor(() => expect(skillsList).toHaveBeenCalled()); // loadSkills 刷新
  });

  it('生效版本展示回滚按钮并可回滚', async () => {
    seed();
    evolutionPromptVersions.mockResolvedValue({
      ok: true,
      items: [
        {
          id: 3,
          skill_id: 'daily_report_check',
          version: 2,
          few_shot: [],
          gain: 1.0,
          status: 'active',
          ts: '2026-08-31T00:00:00',
        },
      ],
    });
    const { getByText } = render(<PromptOptPanel />);
    await waitFor(() => expect(getByText(/生效中/)).toBeTruthy());
    fireEvent.click(getByText('回滚'));
    await waitFor(() => expect(evolutionPromptVersionRollback).toHaveBeenCalledWith(3));
  });
});
