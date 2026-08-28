/**
 * 执行块人性化 + 思维链跳转测试（BUGFIX #153，2026-08-26）。
 *
 * 用户反馈：会话区显示原始「TOOL_CALL write_file」太技术化，且无法点对应动作
 * 跳到思维链。修复：工具名 → 中文动作短语 + 状态词（进行中/已完成/失败）；
 * 点击动作 → traceStore.highlight → ThinkingChainPanel 定位闪烁。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { ThinkingStep } from '@eaide/shared-protocol';
import { ExecutionBlock } from '@/components/chat/ExecutionBlock';
import { ThinkingChainPanel } from '@/components/thinking/ThinkingChainPanel';
import { useThinkingStore } from '@/store/thinkingStore';
import { useTraceStore } from '@/store/traceStore';

// jsdom 未实现 scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

function mkStep(id: string, nodeName: string, toolName: string | null): ThinkingStep {
  return {
    id,
    session_id: 'run-jump-test',
    message_id: null,
    step_index: 0,
    node_name: nodeName,
    thinking: toolName ? `【行动】决定调用工具 ${toolName}` : '【思考】测试',
    thinking_tokens: null,
    tool_calls: toolName ? [{ name: toolName, result: { ok: true } }] : [],
    file_operations: [],
    decision: null,
    tokens_used: null,
    latency_ms: 10,
    created_at: Date.now(),
  };
}

describe('ExecutionBlock 人性化（#153）', () => {
  it('工具名 → 中文动作短语 + 状态词，不再裸露 TOOL_CALL 原样', () => {
    render(
      <ExecutionBlock
        message={{
          id: 'e1',
          role: 'system',
          kind: 'execution',
          category: 'tool_call',
          content: 'write_file',
          status: 'ok',
        }}
      />,
    );
    expect(screen.getByText('工具执行')).toBeTruthy();
    expect(screen.getByText('写入文件')).toBeTruthy();
    expect(screen.getByText('已完成')).toBeTruthy();
  });

  it('未收录工具回退「调用 {name}」；running 显示进行中', () => {
    render(
      <ExecutionBlock
        message={{
          id: 'e2',
          role: 'system',
          kind: 'execution',
          category: 'tool_call',
          content: 'custom_tool_x',
          status: 'running',
        }}
      />,
    );
    expect(screen.getByText('调用 custom_tool_x')).toBeTruthy();
    expect(screen.getByText('进行中')).toBeTruthy();
  });

  it('点击动作触发思维链高亮查询（工具块用工具名）', () => {
    render(
      <ExecutionBlock
        message={{
          id: 'e3',
          role: 'system',
          kind: 'execution',
          category: 'tool_call',
          content: 'office_create',
          status: 'ok',
        }}
      />,
    );
    fireEvent.click(screen.getByText('工具执行'));
    expect(useTraceStore.getState().highlight?.query).toBe('office_create');
    useTraceStore.setState({ highlight: null });
  });
});

describe('思维链跳转定位（#153）', () => {
  afterEach(() => {
    useThinkingStore.setState({ sessionId: null, steps: [], loading: false, error: null });
    useTraceStore.setState({ highlight: null });
  });

  it('highlight 命中工具调用步骤 → 该卡片闪烁并滚动定位', async () => {
    useThinkingStore.setState({
      steps: [mkStep('st1', 'intent', null), mkStep('st2', 'tool_orchestrator', 'write_file')],
    });
    const { container } = render(<ThinkingChainPanel />);

    await act(async () => {
      useTraceStore.getState().setHighlight('write_file');
    });

    expect(container.querySelector('#think-step-st2 .trace-flash')).toBeTruthy();
    expect(container.querySelector('#think-step-st1 .trace-flash')).toBeNull();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it('按节点名命中（非工具类执行块，如自动修复）', async () => {
    useThinkingStore.setState({ steps: [mkStep('st3', 'repair', null)] });
    const { container } = render(<ThinkingChainPanel />);

    await act(async () => {
      useTraceStore.getState().setHighlight('repair');
    });

    expect(container.querySelector('#think-step-st3 .trace-flash')).toBeTruthy();
  });

  it('无匹配步骤时不闪烁不报错', async () => {
    useThinkingStore.setState({ steps: [mkStep('st4', 'intent', null)] });
    const { container } = render(<ThinkingChainPanel />);

    await act(async () => {
      useTraceStore.getState().setHighlight('no_such_tool');
    });

    expect(container.querySelector('.trace-flash')).toBeNull();
  });
});
