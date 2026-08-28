/**
 * shell 流式输出前端归并测试（执行过程可视化 · 阶段三）。
 *
 * 验证：
 * 1. chatStore 按 call_id 归并 shell_chunk（追加 + 256KB 截尾保护）；
 * 2. ShellOutputPanel 流式态显示「执行中」、结束帧后显示退出码徽标；
 * 3. ExecutionBlock 仅对 tool-<callId> 形态的消息挂输出面板。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import type { ChatMessage } from '@eaide/shared-protocol';
import { ExecutionBlock } from '@/components/chat/ExecutionBlock';
import { ExecutionTree } from '@/components/chat/ExecutionTree';
import { useChatStore } from '@/store/chatStore';

function toolMessage(callId: string, status: 'running' | 'ok' = 'running'): ChatMessage {
  return {
    id: `tool-${callId}`,
    role: 'system',
    kind: 'execution',
    category: 'tool_call',
    content: 'shell',
    status,
  };
}

describe('shell_chunk 前端归并（阶段三）', () => {
  beforeEach(() => {
    act(() => {
      useChatStore.setState({ shellOutputByCall: {}, shellExitByCall: {}, toolProgressByCall: {} });
    });
  });

  it('appendShellChunk 按 call_id 追加，互不串台', () => {
    const st = useChatStore.getState();
    st.appendShellChunk('call-a', 'line1\n');
    st.appendShellChunk('call-b', 'other\n');
    st.appendShellChunk('call-a', 'line2\n');
    const s = useChatStore.getState();
    expect(s.shellOutputByCall['call-a']).toBe('line1\nline2\n');
    expect(s.shellOutputByCall['call-b']).toBe('other\n');
  });

  it('超长输出截尾保留最新（256KB 保护）', () => {
    const st = useChatStore.getState();
    st.appendShellChunk('call-big', 'x'.repeat(300000));
    const out = useChatStore.getState().shellOutputByCall['call-big'] ?? '';
    expect(out.length).toBe(262144);
  });

  it('ExecutionBlock 流式态渲染输出面板 + 执行中；结束帧后显示退出码', () => {
    const { rerender } = render(<ExecutionBlock message={toolMessage('call-ui')} />);
    // 无输出时不渲染面板
    expect(screen.queryByText('命令输出')).toBeNull();

    act(() => {
      useChatStore.getState().appendShellChunk('call-ui', 'hello world\n');
    });
    rerender(<ExecutionBlock message={toolMessage('call-ui')} />);
    expect(screen.getByText('命令输出')).toBeTruthy();
    expect(screen.getByText('执行中…')).toBeTruthy();
    expect(screen.getByText(/hello world/)).toBeTruthy();

    act(() => {
      useChatStore.getState().closeShellStream('call-ui', 0);
    });
    rerender(<ExecutionBlock message={toolMessage('call-ui', 'ok')} />);
    expect(screen.getByText('exit 0')).toBeTruthy();
    expect(screen.queryByText('执行中…')).toBeNull();
  });

  it('非零退出码红色徽标展示真实值', () => {
    render(<ExecutionBlock message={toolMessage('call-fail', 'ok')} />);
    act(() => {
      useChatStore.getState().appendShellChunk('call-fail', 'oops\n');
      useChatStore.getState().closeShellStream('call-fail', 3);
    });
    expect(screen.getByText('exit 3')).toBeTruthy();
  });

  it('tool_progress 文案进工具卡副标题（仅 running 态）', () => {
    render(<ExecutionBlock message={toolMessage('call-prog')} />);
    act(() => {
      useChatStore.getState().setToolProgress('call-prog', '正在扫描第 120 个文件…');
    });
    expect(screen.getByText('正在扫描第 120 个文件…')).toBeTruthy();
  });

  it('非 tool- 前缀消息不挂输出面板', () => {
    render(
      <ExecutionBlock
        message={{
          id: 'trace-x',
          role: 'system',
          kind: 'execution',
          category: 'node',
          content: 'intent',
          status: 'ok',
        }}
      />,
    );
    act(() => {
      useChatStore.getState().appendShellChunk('x', 'should-not-show');
    });
    expect(screen.queryByText('should-not-show')).toBeNull();
  });

  it('树形合并（ExecutionTree）路径下也能看到输出面板（渲染缺口回归）', () => {
    render(
      <ExecutionTree
        items={[
          { message: toolMessage('call-tree-1') },
          { message: toolMessage('call-tree-2') },
        ]}
      />,
    );
    act(() => {
      useChatStore.getState().appendShellChunk('call-tree-1', 'tree output line\n');
    });
    expect(screen.getByText('命令输出')).toBeTruthy();
    expect(screen.getByText(/tree output line/)).toBeTruthy();
  });
});
