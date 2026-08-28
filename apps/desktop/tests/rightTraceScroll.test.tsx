/**
 * RightTraceView 贴底跟随回归测试（BUGFIX #151）。
 *
 * 翻车场景（2026-08-26 截图）：思维链防抖刷新一次拉回多条长卡片（单次增高
 * >80px），旧方案在「新内容渲染后」才量距判定贴底 —— 被内容撑离底部被误判成
 * 「用户上滚」，从此不再自动下滑。修复：跟随意图由滚动事件记录（同聊天区
 * CenterChatFlow 模式）+ 内容 ResizeObserver 兜底。
 */
import { afterEach, describe, expect, it } from 'vitest';
import { act, fireEvent, render } from '@testing-library/react';
import type { ThinkingStep } from '@eaide/shared-protocol';
import { RightTraceView } from '@/layouts/RightTraceView';
import { useThinkingStore } from '@/store/thinkingStore';
import { useUIStore } from '@/store/uiStore';

function mkStep(id: string, node: string): ThinkingStep {
  return {
    id,
    session_id: 'run-scroll-test',
    message_id: null,
    step_index: 0,
    node_name: node,
    thinking: '【思考】测试思考内容',
    thinking_tokens: null,
    tool_calls: [],
    file_operations: [],
    decision: null,
    tokens_used: null,
    latency_ms: 12,
    created_at: Date.now(),
  };
}

describe('控制台思维链贴底跟随（#151）', () => {
  afterEach(() => {
    useThinkingStore.setState({ sessionId: null, steps: [], loading: false, error: null });
  });

  it('批量到达一次增高 >80px 仍持续钉底（旧方案在此断随）', async () => {
    useUIStore.setState({ mode: 'full' });
    const { container } = render(<RightTraceView />);
    const el = container.querySelector('.overflow-auto') as HTMLDivElement;
    expect(el).toBeTruthy();

    // jsdom 无布局：用可变高度 mock 几何
    let h = 300;
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => h });
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 200 });

    await act(async () => {
      useThinkingStore.setState({ steps: [mkStep('s1', 'intent')] });
    });
    expect(el.scrollTop).toBe(300);

    // 回归关键点：一次刷新追加多条长卡片（300 → 700，远超 80px 阈值）
    h = 700;
    await act(async () => {
      useThinkingStore.setState({
        steps: [mkStep('s1', 'intent'), mkStep('s2', 'decompose'), mkStep('s3', 'tool_orchestrator')],
      });
    });
    expect(el.scrollTop).toBe(700);
  });

  it('用户上滚回看时不被强制拉回；滚回底部后恢复跟随', async () => {
    useUIStore.setState({ mode: 'full' });
    const { container } = render(<RightTraceView />);
    const el = container.querySelector('.overflow-auto') as HTMLDivElement;
    let h = 1000;
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => h });
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 200 });

    await act(async () => {
      useThinkingStore.setState({ steps: [mkStep('s1', 'intent')] });
    });

    // 模拟用户上滚到顶（距底 800 ≥ 80 → 停止跟随）
    el.scrollTop = 0;
    fireEvent.scroll(el);
    h = 1400;
    await act(async () => {
      useThinkingStore.setState({ steps: [mkStep('s1', 'intent'), mkStep('s2', 'planner')] });
    });
    expect(el.scrollTop).toBe(0);

    // 滚回底部 → 跟随恢复
    el.scrollTop = h - 200;
    fireEvent.scroll(el);
    h = 1800;
    await act(async () => {
      useThinkingStore.setState({
        steps: [mkStep('s1', 'intent'), mkStep('s2', 'planner'), mkStep('s3', 'responder')],
      });
    });
    expect(el.scrollTop).toBe(1800);
  });
});
