/**
 * ActivityBar 测试 —— 资源管理器入口的模式可见性。
 *
 * 2026-11-09（用户要求）：资源管理器（系统资产）只在开发模式展示，
 * 运营 / 审核 / 数据 3 个专家模式不渲染该入口（专家工作台自带左栏）。
 * 横切能力（搜索 / 会话管理）不受模式限制，任何模式都必须点得动。
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ActivityBar } from '@/components/chrome/ActivityBar';
import { useUIStore, type WorkMode } from '@/store/uiStore';

const EXPERT_MODES: WorkMode[] = ['operator', 'auditor', 'analyst'];
const ALL_MODES: WorkMode[] = ['full', ...EXPERT_MODES];

function renderBar(mode: WorkMode): void {
  useUIStore.setState({ mode });
  render(<ActivityBar active="explorer" onChange={() => {}} />);
}

describe('ActivityBar 资源管理器可见性', () => {
  afterEach(() => {
    cleanup();
  });

  it('开发模式：显示资源管理器入口', () => {
    renderBar('full');
    expect(screen.getByTitle(/Explorer/)).toBeTruthy();
  });

  it('运营 / 审核 / 数据模式：不显示资源管理器入口', () => {
    for (const mode of EXPERT_MODES) {
      cleanup();
      renderBar(mode);
      expect(screen.queryByTitle(/Explorer/)).toBeNull();
    }
  });

  it('横切能力（搜索 / 会话管理）在任何模式都保留', () => {
    for (const mode of ALL_MODES) {
      cleanup();
      renderBar(mode);
      expect(screen.getByTitle(/Search/)).toBeTruthy();
      expect(screen.getByTitle(/Sessions/)).toBeTruthy();
    }
  });
});