/**
 * Phase 18 AutonomyToggle：会话级自动模式开关 + 风险确认弹窗流程。
 *
 * 验收点：
 *   - 默认 interactive；点击开启先弹确认框（store 不变）
 *   - 确认后 store.autonomy='auto' 且写授权审计（confirmAutonomy 被调用）
 *   - 取消则保持 interactive
 *   - 已开启时再点直接回落 interactive（无弹窗）
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const confirmAutonomy = vi.fn().mockResolvedValue({ ok: true });

vi.mock('@/ipc/invoke', () => ({
  ipc: { confirmAutonomy: (...args: unknown[]) => confirmAutonomy(...args) },
  invoke: vi.fn(),
}));

import { AutonomyToggle } from '@/components/chat/AutonomyToggle';
import { useChatStore } from '@/store/chatStore';

function allButtons(container: HTMLElement): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll('button'));
}

describe('AutonomyToggle (Phase 18)', () => {
  let container: HTMLDivElement;
  let root: Root;

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) container.remove();
    // 复位 store（zustand 单例跨测试残留）
    useChatStore.setState({ autonomy: 'interactive' });
    confirmAutonomy.mockClear();
  });

  async function render(): Promise<void> {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<AutonomyToggle />);
    });
  }

  it('默认交互模式，点击开启先弹风险确认框（store 不变）', async () => {
    await render();
    expect(useChatStore.getState().autonomy).toBe('interactive');

    const toggle = allButtons(container).find((b) => b.textContent?.includes('交互'));
    expect(toggle).toBeTruthy();
    await act(async () => {
      toggle!.click();
    });
    // 弹窗出现，但 store 仍是 interactive
    expect(document.body.textContent).toContain('我已了解风险，开启自动模式');
    expect(useChatStore.getState().autonomy).toBe('interactive');
  });

  it('确认后进入 auto 并写授权审计', async () => {
    await render();
    const toggle = allButtons(container).find((b) => b.textContent?.includes('交互'));
    await act(async () => {
      toggle!.click();
    });
    const confirm = allButtons(document.body).find((b) =>
      b.textContent?.includes('我已了解风险'),
    );
    expect(confirm).toBeTruthy();
    await act(async () => {
      confirm!.click();
    });
    expect(useChatStore.getState().autonomy).toBe('auto');
    expect(confirmAutonomy).toHaveBeenCalledTimes(1);
  });

  it('取消弹窗保持 interactive', async () => {
    await render();
    const toggle = allButtons(container).find((b) => b.textContent?.includes('交互'));
    await act(async () => {
      toggle!.click();
    });
    const cancel = allButtons(document.body).find((b) => b.textContent?.trim() === '取消');
    expect(cancel).toBeTruthy();
    await act(async () => {
      cancel!.click();
    });
    expect(useChatStore.getState().autonomy).toBe('interactive');
    expect(confirmAutonomy).not.toHaveBeenCalled();
  });

  it('已开启时再点直接回落 interactive（无弹窗、无审计）', async () => {
    useChatStore.setState({ autonomy: 'auto' });
    await render();
    const toggle = allButtons(container).find((b) => b.textContent?.includes('自动'));
    expect(toggle).toBeTruthy();
    await act(async () => {
      toggle!.click();
    });
    expect(useChatStore.getState().autonomy).toBe('interactive');
    expect(document.body.textContent).not.toContain('我已了解风险');
    expect(confirmAutonomy).not.toHaveBeenCalled();
  });
});
