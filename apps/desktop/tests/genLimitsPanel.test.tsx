/**
 * GenLimitsPanel —— 「模型与回复」设置面板（生成限制两级回退）前端回归。
 *
 * 覆盖：
 *   - 挂载后从 Agent 读取并展示当前值
 *   - 修改 + 保存 → routerSetGenLimits 收到稀疏 patch + 成功提示
 *   - 非法输入（<=0 / 非数字）拦截：不发起保存 + 错误提示
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/react';

const agentWaitReady = vi.fn();
const routerGetGenLimits = vi.fn();
const routerSetGenLimits = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    agentWaitReady: (...args: unknown[]) => agentWaitReady(...args),
    routerGetGenLimits: (...args: unknown[]) => routerGetGenLimits(...args),
    routerSetGenLimits: (...args: unknown[]) => routerSetGenLimits(...args),
  },
}));

import { GenLimitsPanel } from '@/views/settings/GenLimitsPanel';

function seed(): void {
  agentWaitReady.mockClear();
  routerGetGenLimits.mockClear();
  routerSetGenLimits.mockClear();
  agentWaitReady.mockResolvedValue({ ready: true });
  routerGetGenLimits.mockResolvedValue({
    ok: true,
    limits: { max_output_tokens: 32768, default_context_window: 32768 },
  });
  routerSetGenLimits.mockResolvedValue({
    ok: true,
    limits: { max_output_tokens: 32768, default_context_window: 32768 },
  });
}

function inputs(container: HTMLElement): HTMLInputElement[] {
  // [0] = 默认上下文长度数字框；[1] = 最大输出长度
  return Array.from(container.querySelectorAll('input')) as HTMLInputElement[];
}

describe('GenLimitsPanel', () => {
  it('挂载后加载并展示当前配置值', async () => {
    seed();
    routerGetGenLimits.mockResolvedValueOnce({
      ok: true,
      limits: { max_output_tokens: 8192, default_context_window: 131072 },
    });
    const { container } = render(<GenLimitsPanel />);
    await waitFor(() => expect(routerGetGenLimits).toHaveBeenCalled());
    await waitFor(() => {
      const [ctxInput, outInput] = inputs(container);
      expect(ctxInput.value).toBe('131072');
      expect(outInput.value).toBe('8192');
    });
  });

  it('修改后保存：稀疏 patch 提交 + 成功提示', async () => {
    seed();
    const { container, getByText } = render(<GenLimitsPanel />);
    await waitFor(() => expect(routerGetGenLimits).toHaveBeenCalled());
    const [ctxInput, outInput] = await waitFor(() => {
      const els = inputs(container);
      expect(els[1].value).toBe('32768');
      return els;
    });

    fireEvent.change(outInput, { target: { value: '4096' } });
    fireEvent.change(ctxInput, { target: { value: '65536' } });
    fireEvent.click(getByText('保存'));

    await waitFor(() =>
      expect(routerSetGenLimits).toHaveBeenCalledWith({
        max_output_tokens: 4096,
        default_context_window: 65536,
      }),
    );
    await waitFor(() => expect(getByText(/已保存并热生效/)).toBeTruthy());
  });

  it('非法输入被拦截：不发请求 + 错误提示', async () => {
    seed();
    const { container, getByText } = render(<GenLimitsPanel />);
    await waitFor(() => expect(routerGetGenLimits).toHaveBeenCalled());
    const [, outInput] = await waitFor(() => {
      const els = inputs(container);
      expect(els[1].value).toBe('32768');
      return els;
    });

    fireEvent.change(outInput, { target: { value: '0' } });
    fireEvent.click(getByText('保存'));
    await waitFor(() => expect(getByText(/最大输出长度需为/)).toBeTruthy());
    expect(routerSetGenLimits).not.toHaveBeenCalled();
  });
});
