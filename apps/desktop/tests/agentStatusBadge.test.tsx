/**
 * AgentStatusBadge 测试 —— 状态栏 / 顶栏的 Agent 运行态徽标。
 *
 * 覆盖：
 *   - busy（运行中）→ 文案「运行中」+ 动态动画 SVG（缺口弧 + 呼吸核心）
 *   - ready / idle（未活动）→ 文案「未活动」+ 静态待机图标（无动画元素）
 *   - error / unknown（未就绪）→ 文案「未就绪」+ 静态叉号图标
 *   - 配色随状态变化（绿 / 灰 / 红）
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AGENT_STATUS_META, AgentStatusBadge } from '@/components/chrome/AgentStatusBadge';
import { useUIStore } from '@/store/uiStore';

describe('AgentStatusBadge', () => {
  beforeEach(() => {
    useUIStore.setState({ agentStatus: 'unknown' });
  });

  afterEach(() => {
    cleanup();
  });

  it('运行中（busy）：显示「运行中」+ 动态动画 SVG（自转弧 + 呼吸核心）', () => {
    useUIStore.setState({ agentStatus: 'busy' });
    const { container } = render(<AgentStatusBadge />);

    expect(screen.getByTestId('agent-status-badge').textContent).toContain('运行中');
    // 动画元素：缺口弧（agent-status-arc）+ 呼吸核心（agent-status-core）
    expect(container.querySelector('.agent-status-arc')).not.toBeNull();
    expect(container.querySelector('.agent-status-core')).not.toBeNull();
  });

  it('未活动（ready / idle）：显示「未活动」+ 静态图标（无动画元素）', () => {
    for (const status of ['ready', 'idle'] as const) {
      useUIStore.setState({ agentStatus: status });
      const { container, unmount } = render(<AgentStatusBadge />);

      expect(screen.getByTestId('agent-status-badge').textContent).toContain('未活动');
      expect(container.querySelector('.agent-status-arc')).toBeNull();
      expect(container.querySelector('.agent-status-core')).toBeNull();
      // 待机圆环 = 2 个 circle（外环 + 中心点），无 path
      expect(container.querySelectorAll('circle').length).toBe(2);
      expect(container.querySelectorAll('path').length).toBe(0);
      unmount();
    }
  });

  it('未就绪（error）：显示「未就绪」+ 静态叉号图标（红色）', () => {
    useUIStore.setState({ agentStatus: 'error' });
    const { container } = render(<AgentStatusBadge />);

    expect(screen.getByTestId('agent-status-badge').textContent).toContain('未就绪');
    expect(container.querySelector('.agent-status-arc')).toBeNull();
    // 叉号圆圈 = 1 个 circle + 1 个 path（双 M 子路径画叉）
    expect(container.querySelectorAll('circle').length).toBe(1);
    expect(container.querySelectorAll('path').length).toBe(1);
  });

  it('未探测（unknown）：显示「未就绪」但用灰色，避免启动瞬间误报红色', () => {
    useUIStore.setState({ agentStatus: 'unknown' });
    render(<AgentStatusBadge />);

    expect(screen.getByTestId('agent-status-badge').textContent).toContain('未就绪');
    expect(AGENT_STATUS_META.unknown.color).toBe('#8a8a8a');
    expect(AGENT_STATUS_META.error.color).toBe('#cd3131');
  });

  it('配色随状态：运行中黑 / 未活动灰 / 未就绪红', () => {
    expect(AGENT_STATUS_META.busy.color).toBe('#1f1f1f');
    expect(AGENT_STATUS_META.ready.color).toBe('#616161');
    expect(AGENT_STATUS_META.idle.color).toBe('#616161');
  });

  it('悬浮提示随状态变化，说明当前含义', () => {
    useUIStore.setState({ agentStatus: 'busy' });
    const { unmount } = render(<AgentStatusBadge />);
    expect(screen.getByTestId('agent-status-badge').getAttribute('title')).toContain('正在处理任务');
    unmount();

    useUIStore.setState({ agentStatus: 'error' });
    render(<AgentStatusBadge />);
    expect(screen.getByTestId('agent-status-badge').getAttribute('title')).toContain('未就绪');
  });
});
