/**
 * TokenUsageBadge 测试 —— 「Agent: 就绪」旁的 Token 用量徽章。
 *
 * 覆盖：
 *   - 数量/速率/费用紧凑格式化（k/M 单位、负数与非有限数防御）
 *   - 徽章本体显示实时速率（↑上传/↓下载）+ 调用次数
 *   - 悬浮卡片含当日总量、调用次数、总费用与按模型明细
 *   - Agent 未就绪（请求失败）时渲染占位「--」且无卡片
 */
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  TokenUsageBadge,
  formatCost,
  formatTokenCount,
  formatTokenRate,
} from '@/components/chrome/TokenUsageBadge';

const tokenUsageGet = vi.fn();

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    tokenUsageGet: () => tokenUsageGet(),
  },
}));

describe('formatTokenCount', () => {
  it('千以下原样、千以上 k、百万以上 M', () => {
    expect(formatTokenCount(0)).toBe('0');
    expect(formatTokenCount(999)).toBe('999');
    expect(formatTokenCount(12345)).toBe('12.3k');
    expect(formatTokenCount(2_300_000)).toBe('2.3M');
  });

  it('负数与非有限数防御为 0', () => {
    expect(formatTokenCount(-5)).toBe('0');
    expect(formatTokenCount(Number.NaN)).toBe('0');
  });
});

describe('formatTokenRate', () => {
  it('零与负数显示 0；小数保留 1 位；大数走紧凑格式', () => {
    expect(formatTokenRate(0)).toBe('0');
    expect(formatTokenRate(-3)).toBe('0');
    expect(formatTokenRate(2.34)).toBe('2.3');
    expect(formatTokenRate(123)).toBe('123');
    expect(formatTokenRate(4500)).toBe('4.5k');
  });
});

describe('formatCost', () => {
  it('小额保留 4 位，大额保留 2 位，非法值归 0', () => {
    expect(formatCost(0)).toBe('0');
    expect(formatCost(-1)).toBe('0');
    expect(formatCost(0.0012)).toBe('0.0012');
    expect(formatCost(0.0123)).toBe('0.01');
    expect(formatCost(1.5)).toBe('1.50');
  });
});

describe('TokenUsageBadge', () => {
  beforeEach(() => {
    tokenUsageGet.mockReset();
  });

  it('徽章显示速率与调用次数，悬浮卡片含当日明细与费用', async () => {
    tokenUsageGet.mockResolvedValue({
      day: '2026-08-07',
      window_seconds: 30,
      rate_upload_per_s: 12.0,
      rate_download_per_s: 3.5,
      rate_calls_per_s: 0.03,
      today_upload_tokens: 12345,
      today_download_tokens: 678,
      today_total_tokens: 13023,
      today_call_count: 42,
      today_cost_total: 0.0123,
      cost_by_model: { 'gpt-4o': 0.01, 'DeepSeek-70B': 0.0023 },
    });
    render(<TokenUsageBadge />);
    const badge = screen.getByTestId('token-usage-badge');
    await waitFor(() => {
      // 徽章本体：速率 + 调用次数（当日总量移入悬浮卡片）
      expect(badge.textContent).toContain('↑12 ↓3.5 tok/s · 42 次');
    });
    // 悬浮卡片：当日累计 + 费用明细
    const card = screen.getByTestId('token-usage-card');
    expect(card.textContent).toContain('↑12.3k / ↓678');
    expect(card.textContent).toContain('当日调用次数42 次');
    expect(card.textContent).toContain('当日费用（总）0.01');
    expect(card.textContent).toContain('· gpt-4o0.01');
    expect(card.textContent).toContain('· DeepSeek-70B0.0023');
  });

  it('请求失败时显示占位且不渲染卡片', async () => {
    tokenUsageGet.mockRejectedValue(new Error('agent not ready'));
    render(<TokenUsageBadge />);
    expect(screen.getByTestId('token-usage-badge').textContent).toBe('↑-- ↓-- tok/s');
    expect(screen.queryByTestId('token-usage-card')).toBeNull();
  });
});
