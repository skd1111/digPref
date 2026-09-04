/**
 * AgentReadyGate 测试 —— Agent 未就绪时的「整屏模糊 + 禁交互」启动闸门。
 *
 * 覆盖：
 *   - booting：渲染遮罩卡片 + body 挂 .agent-booting（globals.css 借此模糊禁点 #root）
 *   - ready：/health 2xx → 遮罩消失、body 类名摘掉、状态栏立刻转「就绪」
 *   - failed：超时/异常 → 错误文案 + 重试 / 重启 Agent / 查看日志 / 跳过闸门四个出口
 *   - 键盘闸门：遮罩外的全局按键被 preventDefault + stopPropagation，遮罩内放行
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentReadyGate } from '@/components/chrome/AgentReadyGate';
import {
  BOOT_FIRST_TIMEOUT_S,
  BOOT_REPROBE_INTERVAL_MS,
  __resetAgentBootForTest,
} from '@/lib/agentBoot';
import { useUIStore } from '@/store/uiStore';

const agentWaitReady = vi.fn();
const agentRestartNow = vi.fn();
const agentReadLog = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    agentWaitReady: (timeoutS?: number) => agentWaitReady(timeoutS),
    agentRestartNow: () => agentRestartNow(),
    agentReadLog: (lines?: number) => agentReadLog(lines),
  },
}));

/** 首轮等待挂住不返回（模拟 Agent 还在冷启动） */
function pendingBoot(): void {
  agentWaitReady.mockImplementation(() => new Promise(() => undefined));
}

describe('AgentReadyGate', () => {
  beforeEach(() => {
    agentWaitReady.mockReset();
    agentRestartNow.mockReset();
    agentReadLog.mockReset();
    __resetAgentBootForTest();
    useUIStore.setState({
      agentBootState: 'booting',
      agentBootError: null,
      agentBootElapsedMs: null,
      agentStatus: 'unknown',
    });
    document.body.classList.remove('agent-booting');
  });

  afterEach(() => {
    cleanup();
    // 失败态会开 3s 后台复探定时器，务必清掉，否则跨用例继续打 mock
    __resetAgentBootForTest();
  });

  it('Agent 未就绪：渲染遮罩 + body 挂 agent-booting + 阻塞等待 30s', async () => {
    pendingBoot();
    render(<AgentReadyGate />);

    expect(screen.getByRole('dialog', { name: 'Agent 启动中' }).textContent).toContain(
      '正在启动 Agent…',
    );
    expect(document.body.classList.contains('agent-booting')).toBe(true);
    await waitFor(() => expect(agentWaitReady).toHaveBeenCalledWith(BOOT_FIRST_TIMEOUT_S));
  });

  it('Agent 就绪：遮罩自动放行、body 类名摘掉、状态栏转「就绪」', async () => {
    agentWaitReady.mockResolvedValue({ ready: true, elapsed_ms: 4200 });
    render(<AgentReadyGate />);

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.body.classList.contains('agent-booting')).toBe(false);
    expect(useUIStore.getState().agentBootState).toBe('ready');
    expect(useUIStore.getState().agentBootElapsedMs).toBe(4200);
    expect(useUIStore.getState().agentStatus).toBe('ready');
  });

  it('首轮超时：切错误态并给出重试 / 重启 / 日志 / 跳过四个出口', async () => {
    agentWaitReady.mockResolvedValue({
      ready: false,
      elapsed_ms: 30_000,
      error: 'error sending request for url (http://127.0.0.1:8765/health)',
    });
    render(<AgentReadyGate />);

    const dialog = await screen.findByRole('dialog', { name: 'Agent 启动异常' });
    expect(dialog.textContent).toContain('✗ Agent 未就绪');
    expect(dialog.textContent).toContain('error sending request');
    expect(dialog.textContent).toContain('等待 30.0s');
    expect(useUIStore.getState().agentBootState).toBe('failed');
    // 仍未就绪 → 遮罩不撤、模糊不放
    expect(document.body.classList.contains('agent-booting')).toBe(true);
    expect(screen.getByRole('button', { name: /重试/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: '⟳ 重启 Agent' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '▾ 查看日志' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '跳过闸门' })).toBeTruthy();
  });

  it('失败态点「重试」：重新走一轮阻塞等待，遮罩回到 booting', async () => {
    agentWaitReady
      .mockResolvedValueOnce({ ready: false, error: 'connection refused' })
      .mockResolvedValueOnce({ ready: true, elapsed_ms: 900 });
    render(<AgentReadyGate />);

    fireEvent.click(await screen.findByRole('button', { name: /重试/ }));
    await waitFor(() => expect(agentWaitReady).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(useUIStore.getState().agentBootState).toBe('ready');
  });

  it('失败态点「重启 Agent」：先杀端口再等就绪', async () => {
    agentRestartNow.mockResolvedValue({ ok: true, port_freed: true });
    agentWaitReady
      .mockResolvedValueOnce({ ready: false, error: 'timeout' })
      .mockResolvedValue({ ready: true, elapsed_ms: 3000 });
    render(<AgentReadyGate />);

    fireEvent.click(await screen.findByRole('button', { name: '⟳ 重启 Agent' }));
    await waitFor(() => expect(agentRestartNow).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(useUIStore.getState().agentBootState).toBe('ready');
  });

  it('失败态点「查看日志」：展示 eaide.log 末尾内容', async () => {
    agentWaitReady.mockResolvedValue({ ready: false, error: 'timeout' });
    agentReadLog.mockResolvedValue({
      ok: true,
      path: 'C:/logs/eaide.log',
      tail: '[agent_manager] spawn python failed',
      line_count: 60,
    });
    render(<AgentReadyGate />);

    fireEvent.click(await screen.findByRole('button', { name: '▾ 查看日志' }));
    expect(await screen.findByText(/spawn python failed/)).toBeTruthy();
    expect(agentReadLog).toHaveBeenCalledWith(60);
  });

  it('首轮失败后后台每 3s 复探，Agent 起来自动放行（无需用户点击）', async () => {
    agentWaitReady
      .mockResolvedValueOnce({ ready: false, error: 'timeout' })
      .mockResolvedValue({ ready: true, elapsed_ms: 100 });
    render(<AgentReadyGate />);

    await screen.findByRole('dialog', { name: 'Agent 启动异常' });
    await waitFor(() => expect(useUIStore.getState().agentBootState).toBe('ready'), {
      timeout: BOOT_REPROBE_INTERVAL_MS + 3000,
    });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.body.classList.contains('agent-booting')).toBe(false);
    // 复探用短探测窗（3s），不再占着首轮 30s
    expect(agentWaitReady).toHaveBeenLastCalledWith(3);
  }, 15_000);

  it('闸门卸载后停掉后台复探（不留定时器继续打 IPC）', async () => {
    agentWaitReady.mockResolvedValue({ ready: false, error: 'timeout' });
    const { unmount } = render(<AgentReadyGate />);
    await screen.findByRole('dialog', { name: 'Agent 启动异常' });

    unmount();
    const callsAtUnmount = agentWaitReady.mock.calls.length;
    await new Promise((r) => setTimeout(r, BOOT_REPROBE_INTERVAL_MS + 500));
    expect(agentWaitReady.mock.calls.length).toBe(callsAtUnmount);
  }, 15_000);

  it('失败态点「跳过闸门」：强制放行（IPC 不可用等环境的逃生口）', async () => {
    agentWaitReady.mockRejectedValue(new Error('__TAURI_INTERNALS__ is undefined'));
    render(<AgentReadyGate />);

    const dialog = await screen.findByRole('dialog', { name: 'Agent 启动异常' });
    expect(dialog.textContent).toContain('__TAURI_INTERNALS__ is undefined');
    fireEvent.click(screen.getByRole('button', { name: '跳过闸门' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(useUIStore.getState().agentBootState).toBe('ready');
    expect(document.body.classList.contains('agent-booting')).toBe(false);
  });

  it('键盘闸门：遮罩外的全局按键被拦下，遮罩内按键放行', async () => {
    pendingBoot();
    render(<AgentReadyGate />);
    await screen.findByRole('dialog', { name: 'Agent 启动中' });

    // 遮罩外（如 body / 被模糊的 #root）按 Ctrl+Shift+P → 拦下，命令面板开不了
    const outside = new KeyboardEvent('keydown', {
      key: 'P',
      ctrlKey: true,
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    });
    document.body.dispatchEvent(outside);
    expect(outside.defaultPrevented).toBe(true);

    // 遮罩内按键（按钮 Enter/Space、日志选中复制）必须放行
    const dialog = screen.getByRole('dialog', { name: 'Agent 启动中' });
    const inside = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    dialog.dispatchEvent(inside);
    expect(inside.defaultPrevented).toBe(false);
  });

  it('就绪后不再拦键盘（闸门彻底退出）', async () => {
    agentWaitReady.mockResolvedValue({ ready: true, elapsed_ms: 10 });
    render(<AgentReadyGate />);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    const ev = new KeyboardEvent('keydown', { key: 'P', bubbles: true, cancelable: true });
    document.body.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
  });
});
