/**
 * 执行步骤树形合并 + 右侧精确定位测试（2026-08-27）。
 *
 * 用户反馈「多个工具调用不能合并吗，一点都不人性化」：
 *   1. 连续执行步骤合并为一棵可折叠树（摘要行 + 缩进子项）
 *   2. 进行中的树自动展开、全部完成自动收起
 *   3. 点子项 → 右侧思维链按 occurrence 精确定位（同名多次调用不串）
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { ChatMessage, ThinkingStep } from '@eaide/shared-protocol';
import { ExecutionTree } from '@/components/chat/ExecutionTree';
import { groupExecutionSteps } from '@/lib/executionGrouping';
import { ThinkingChainPanel } from '@/components/thinking/ThinkingChainPanel';
import { useThinkingStore } from '@/store/thinkingStore';
import { useTraceStore } from '@/store/traceStore';

// jsdom 未实现 scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

function mkExec(id: string, tool: string, status: 'running' | 'ok' | 'err'): ChatMessage {
  return {
    id,
    role: 'system',
    kind: 'execution',
    category: 'tool_call',
    content: tool,
    status,
  };
}

function mkMsg(id: string, role: 'user' | 'assistant', content: string): ChatMessage {
  return { id, role, content };
}

function mkStep(id: string, toolName: string): ThinkingStep {
  return {
    id,
    session_id: 'run-tree-test',
    message_id: null,
    step_index: 0,
    node_name: 'tool_orchestrator',
    thinking: `【行动】调用 ${toolName}`,
    thinking_tokens: null,
    tool_calls: [{ name: toolName, result: { ok: true } }],
    file_operations: [],
    decision: null,
    tokens_used: null,
    latency_ms: 10,
    created_at: Date.now(),
  };
}

describe('执行步骤分组（树形合并）', () => {
  it('连续多条执行步骤 → 一棵树（摘要含全部子项）', () => {
    const items = groupExecutionSteps([
      mkExec('e1', 'read_file', 'ok'),
      mkExec('e2', 'write_file', 'ok'),
      mkExec('e3', 'shell', 'ok'),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe('tree');
    if (items[0].type === 'tree') {
      expect(items[0].key).toBe('e1');
      expect(items[0].items).toHaveLength(3);
    }
  });

  it('单条执行步骤保持原样（不套树壳）', () => {
    const items = groupExecutionSteps([mkExec('e1', 'read_file', 'ok')]);
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe('msg');
    if (items[0].type === 'msg') expect(items[0].occurrence).toBe(1);
  });

  it('非执行消息把树切断分段（审批卡/追问/终答天然分界）', () => {
    const items = groupExecutionSteps([
      mkExec('e1', 'read_file', 'ok'),
      mkExec('e2', 'write_file', 'ok'),
      mkMsg('m1', 'assistant', '请确认'),
      mkExec('e3', 'shell', 'ok'),
      mkExec('e4', 'shell', 'ok'),
    ]);
    expect(items.map((i) => i.type)).toEqual(['tree', 'msg', 'tree']);
  });

  it('occurrence 按同名工具全局递增（1 基，从早到晚）', () => {
    const items = groupExecutionSteps([
      mkExec('e1', 'write_file', 'ok'),
      mkExec('e2', 'shell', 'ok'),
      mkExec('e3', 'write_file', 'ok'),
    ]);
    expect(items).toHaveLength(1);
    if (items[0].type === 'tree') {
      expect(items[0].items.map((i) => i.occurrence)).toEqual([1, 1, 2]);
    }
  });
});

describe('ExecutionTree 渲染与折叠', () => {
  it('全部完成 → 默认收起只显示摘要；点击展开看子项', () => {
    render(
      <ExecutionTree
        items={[
          { message: mkExec('e1', 'read_file', 'ok'), occurrence: 1 },
          { message: mkExec('e2', 'write_file', 'ok'), occurrence: 1 },
        ]}
      />,
    );
    expect(screen.getByText('执行过程')).toBeTruthy();
    expect(screen.getByText('2 步 · 全部完成')).toBeTruthy();
    // 收起态：子项不渲染
    expect(screen.queryByText('读取文件')).toBeNull();
    fireEvent.click(screen.getByText('执行过程'));
    expect(screen.getByText('读取文件')).toBeTruthy();
    expect(screen.getByText('写入文件')).toBeTruthy();
  });

  it('有进行中步骤 → 自动展开；摘要显示进度', () => {
    render(
      <ExecutionTree
        items={[
          { message: mkExec('e1', 'read_file', 'ok'), occurrence: 1 },
          { message: mkExec('e2', 'shell', 'running'), occurrence: 1 },
        ]}
      />,
    );
    expect(screen.getByText('1/2 步')).toBeTruthy();
    expect(screen.getByText('读取文件')).toBeTruthy();
    expect(screen.getByText('执行命令')).toBeTruthy();
  });

  it('点子项动作 → 高亮查询携带 occurrence（同名多次调用精确定位）', () => {
    // 连续同名会被同类合并（×N），用 read→write→shell→write 隔开保持两行，
    // 验证第二个 write（非合并行，labels[3]）仍携带 occurrence=2
    render(
      <ExecutionTree
        items={[
          { message: mkExec('e1', 'read_file', 'ok'), occurrence: 1 },
          { message: mkExec('e2', 'write_file', 'ok'), occurrence: 1 },
          { message: mkExec('e3', 'shell', 'ok'), occurrence: 1 },
          { message: mkExec('e4', 'write_file', 'running'), occurrence: 2 },
        ]}
      />,
    );
    const labels = screen.getAllByText('工具执行');
    fireEvent.click(labels[3]);
    const hl = useTraceStore.getState().highlight;
    expect(hl?.query).toBe('write_file');
    expect(hl?.occurrence).toBe(2);
    useTraceStore.setState({ highlight: null });
  });
});

describe('同类工具合并（×N 压缩，2026-08-27）', () => {
  it('连续同名工具 → 一行 + ×N 徽标，不再逐条刷屏', () => {
    render(
      <ExecutionTree
        items={[
          { message: mkExec('e1', 'shell', 'ok'), occurrence: 1 },
          { message: mkExec('e2', 'shell', 'ok'), occurrence: 2 },
          { message: mkExec('e3', 'shell', 'ok'), occurrence: 3 },
        ]}
      />,
    );
    fireEvent.click(screen.getByText('执行过程'));
    // 只有一行「执行命令」+ ×3 徽标；摘要仍按真实步数统计（3 步）
    expect(screen.getAllByText('执行命令')).toHaveLength(1);
    expect(screen.getByText('×3')).toBeTruthy();
    expect(screen.getByText('3 步 · 全部完成')).toBeTruthy();
  });

  it('不同工具交替不合并（只压连续同类）', () => {
    render(
      <ExecutionTree
        items={[
          { message: mkExec('e1', 'shell', 'ok'), occurrence: 1 },
          { message: mkExec('e2', 'read_file', 'ok'), occurrence: 1 },
          { message: mkExec('e3', 'shell', 'ok'), occurrence: 2 },
        ]}
      />,
    );
    fireEvent.click(screen.getByText('执行过程'));
    expect(screen.getAllByText('执行命令')).toHaveLength(2);
    expect(screen.getAllByText('读取文件')).toHaveLength(1);
    expect(screen.queryByText(/×/)).toBeNull();
  });

  it('合并行聚合状态：任一失败标红，任一在跑转圈', () => {
    render(
      <ExecutionTree
        items={[
          { message: mkExec('e1', 'shell', 'ok'), occurrence: 1 },
          { message: mkExec('e2', 'shell', 'err'), occurrence: 2 },
          { message: mkExec('e3', 'read_file', 'running'), occurrence: 1 },
        ]}
      />,
    );
    // shell 合并行标红（含失败）；有失败时摘要优先显示失败口径（3 步 · 1 失败）
    expect(screen.getByText('×2')).toBeTruthy();
    expect(screen.getByText('✗')).toBeTruthy();
    expect(screen.getByText('3 步 · 1 失败')).toBeTruthy();
  });
});

describe('思维链按 occurrence 精确定位', () => {
  afterEach(() => {
    useThinkingStore.setState({ sessionId: null, steps: [], loading: false, error: null });
    useTraceStore.setState({ highlight: null });
  });

  async function renderAndHighlight(occurrence?: number): Promise<HTMLElement> {
    useThinkingStore.setState({
      steps: [mkStep('st1', 'write_file'), mkStep('st2', 'write_file')],
    });
    const { container } = render(<ThinkingChainPanel />);
    await act(async () => {
      useTraceStore.getState().setHighlight('write_file', occurrence);
    });
    return container;
  }

  it('occurrence=1 → 命中第一个同名步骤', async () => {
    const container = await renderAndHighlight(1);
    expect(container.querySelector('#think-step-st1 .trace-flash')).toBeTruthy();
    expect(container.querySelector('#think-step-st2 .trace-flash')).toBeNull();
  });

  it('occurrence=2 → 命中第二个同名步骤', async () => {
    const container = await renderAndHighlight(2);
    expect(container.querySelector('#think-step-st2 .trace-flash')).toBeTruthy();
    expect(container.querySelector('#think-step-st1 .trace-flash')).toBeNull();
  });

  it('缺省（旧行为）→ 回退最新一条', async () => {
    const container = await renderAndHighlight();
    expect(container.querySelector('#think-step-st2 .trace-flash')).toBeTruthy();
  });

  it('occurrence 越界 → 回退最新一条不报错', async () => {
    const container = await renderAndHighlight(9);
    expect(container.querySelector('#think-step-st2 .trace-flash')).toBeTruthy();
  });
});
