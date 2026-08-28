/**
 * ShellOutputPanel —— shell 命令流式输出面板（执行过程可视化 · 阶段三）。
 *
 * 消费 chatStore 的 shellOutputByCall 缓冲（shell_chunk 事件按 call_id 归并）：
 *   - 流式态（结束帧未到）：底部呼吸点提示仍在跑，自动滚到底；
 *   - 结束帧到达（shellExitByCall 有值）：尾部展示退出码徽标（0 绿 / 非 0 红）。
 * 样式纪律：等宽字体 + 深底浅字（终端质感），高度上限内滚动，不撑爆对话流。
 */
import { useEffect, useRef } from 'react';
import { useChatStore } from '../../store/chatStore';

interface ShellOutputPanelProps {
  callId: string;
}

export function ShellOutputPanel({ callId }: ShellOutputPanelProps): JSX.Element | null {
  const output = useChatStore((s) => s.shellOutputByCall[callId]);
  const exitCode = useChatStore((s) => s.shellExitByCall[callId]);
  const scrollRef = useRef<HTMLPreElement | null>(null);

  // 流式期间自动滚到最新输出（用户手动上滚时不抢 —— 简单起见仅在流式态强制跟随）
  useEffect(() => {
    if (exitCode == null && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [output, exitCode]);

  if (!output && exitCode == null) return null;

  const streaming = exitCode == null;
  return (
    <div className="mx-3 mb-2 rounded-md" style={{ backgroundColor: '#1f2430' }}>
      <div className="flex items-center gap-2 px-2 pt-1.5 text-[10px]" style={{ color: '#9ca3af' }}>
        <span>命令输出</span>
        {streaming && (
          <span className="inline-flex items-center gap-1" style={{ color: '#fbbf24' }}>
            <span
              className="animate-spin-ring inline-block rounded-full"
              style={{ width: 8, height: 8, border: '1.5px solid #4b5563', borderTopColor: '#fbbf24' }}
            />
            执行中…
          </span>
        )}
        {!streaming && (
          <span
            className="rounded px-1 py-px font-mono"
            style={{
              color: exitCode === 0 ? '#34d399' : '#f87171',
              backgroundColor: exitCode === 0 ? 'rgba(52,211,153,0.12)' : 'rgba(248,113,113,0.12)',
            }}
          >
            exit {exitCode}
          </span>
        )}
      </div>
      <pre
        ref={scrollRef}
        className="max-h-40 overflow-y-auto whitespace-pre-wrap break-all px-2 pb-2 pt-1 font-mono text-[10px] leading-relaxed"
        style={{ color: '#e5e7eb' }}
      >
        {output || '(无输出)'}
      </pre>
    </div>
  );
}
