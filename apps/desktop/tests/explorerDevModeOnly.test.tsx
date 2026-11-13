/**
 * 资源管理器只在开发模式展示（2026-11-09 用户要求）。
 *
 * 覆盖两条链路：
 *   1. ActivityBar 的资源管理器入口（Explorer / 系统资产）—— 非开发模式不渲染；
 *   2. 左侧栏的 explorer 子切换（系统资产 / 文件列表 / 系统功能点）—— 非开发模式不渲染。
 *
 * 横切能力（搜索 / 会话管理）在任何模式都保留，不受影响。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

vi.mock('@tauri-apps/api/event', () => ({
  listen: async () => () => {},
}));

import { App } from '@/App';
import { useUIStore, type WorkMode } from '@/store/uiStore';

const EXPERT_MODES: WorkMode[] = ['operator', 'auditor', 'analyst'];

describe('资源管理器只在开发模式展示', () => {
  let container: HTMLDivElement;
  let root: Root;

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) {
      container.remove();
    }
  });

  async function mountApp(mode: WorkMode): Promise<void> {
    useUIStore.setState({ mode, activityId: 'explorer' });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<App />);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  function explorerEntry(): Element | null {
    return container.querySelector('button[title^="Explorer"]');
  }

  function devSwitcher(): Element | null {
    return container.querySelector('[aria-label="开发模式左侧栏内容"]');
  }

  it('开发模式：ActivityBar 有资源管理器入口 + 左侧栏有 explorer 子切换', async () => {
    await mountApp('full');
    expect(explorerEntry()).not.toBeNull();
    expect(devSwitcher()).not.toBeNull();
  });

  it('运营 / 审核 / 数据模式：既没有资源管理器入口，也没有 explorer 子切换', async () => {
    for (const mode of EXPERT_MODES) {
      await mountApp(mode);
      expect(explorerEntry()).toBeNull();
      expect(devSwitcher()).toBeNull();
      await act(async () => {
        root.unmount();
      });
      root = undefined as unknown as Root;
      container.remove();
    }
  });

  it('横切能力（搜索 / 会话管理）在任何模式都保留入口', async () => {
    for (const mode of ['full', ...EXPERT_MODES] as WorkMode[]) {
      await mountApp(mode);
      expect(container.querySelector('button[title^="Search"]')).not.toBeNull();
      expect(container.querySelector('button[title^="Sessions"]')).not.toBeNull();
      await act(async () => {
        root.unmount();
      });
      root = undefined as unknown as Root;
      container.remove();
    }
  });
});