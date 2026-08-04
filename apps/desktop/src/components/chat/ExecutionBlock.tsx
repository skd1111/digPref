/**
 * ExecutionBlock —— 单条执行链路 step 的渲染（Codex/Claude 风格）。
 *
 * 一行一行显示，按 category 着色，按 status 显示 ✓ / ✗ / ⏳ 图标。
 * 默认展开；用户可单击行折叠 / 展开。
 */
import { useState } from 'react';
import type { ChatMessage } from '@eaide/shared-protocol';

interface ExecutionBlockProps {
  message: ChatMessage;
}

const CATEGORY_COLORS: Record<string, string> = {
  intent: '#059669',          // 青
  plan: '#0451a5',            // 蓝
  repair: '#795e26',          // 黄
  responder: '#c586c0',       // 紫
  summarise: '#c586c0',
  tool_call: '#0b6bcb',        // 浅蓝
  tool_result: '#0b6bcb',
  hitl_gate: '#cd3131',        // 橙红
  codenav: '#059669',
  'codenav.explain': '#059669',
  'codenav.jump': '#059669',
  log: '#616161',
};

const STATUS_ICON: Record<string, string> = {
  running: '⏳',
  ok: '✓',
  err: '✗',
};

function colorFor(category: string | undefined): string {
  if (!category) return '#616161';
  return CATEGORY_COLORS[category] ?? '#616161';
}

export function ExecutionBlock({ message }: ExecutionBlockProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const color = colorFor(message.category);
  const icon = STATUS_ICON[message.status ?? 'ok'] ?? '✓';
  const latency =
    message.latencyMs != null ? ` · ${message.latencyMs}ms` : '';

  return (
    <div
      className="my-1 rounded font-mono text-2xs"
      style={{
        backgroundColor: '#f3f3f3',
        borderLeft: `3px solid ${color}`,
      }}
    >
      {/* 一行式 summary —— 默认显示；点击展开 details */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-[#2a2d2e]"
        style={{ color: '#1f1f1f' }}
      >
        <span
          className="flex-shrink-0 font-bold"
          style={{ color, width: 14 }}
          title={message.status ?? 'ok'}
        >
          {icon}
        </span>
        <span
          className="flex-shrink-0 font-semibold uppercase tracking-wider"
          style={{ color }}
        >
          {message.category ?? 'step'}
        </span>
        <span className="flex-1 truncate">{message.content}</span>
        {latency && (
          <span className="flex-shrink-0" style={{ color: '#616161' }}>
            {latency}
          </span>
        )}
        <span className="flex-shrink-0" style={{ color: '#616161' }}>
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open && (
        <div
          className="border-t px-3 py-2"
          style={{ borderColor: '#e0e0e0', color: '#6e6e6e' }}
        >
          {/* 详情：完整 content + runId，方便用户复制 */}
          <pre className="whitespace-pre-wrap break-all font-mono text-[10px]">
            {message.content}
          </pre>
          {message.runId && (
            <div className="mt-1 text-[10px]" style={{ color: '#6a6a6a' }}>
              run_id={message.runId}
            </div>
          )}
        </div>
      )}
    </div>
  );
}