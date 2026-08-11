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
  intent: '#0d9488',          // 青
  plan: '#2563eb',            // 蓝
  repair: '#d97706',          // 琥珀
  responder: '#8b5cf6',       // 紫
  summarise: '#8b5cf6',
  tool_call: '#0891b2',       // 深青
  tool_result: '#0891b2',
  hitl_gate: '#dc2626',       // 红
  codenav: '#0d9488',
  'codenav.explain': '#0d9488',
  'codenav.jump': '#0d9488',
  log: '#6b7280',
};

const STATUS_ICON: Record<string, string> = {
  running: '',   // running 用细环 spinner 动效，不用 emoji
  ok: '✓',
  err: '✗',
};

function colorFor(category: string | undefined): string {
  if (!category) return '#6b7280';
  return CATEGORY_COLORS[category] ?? '#6b7280';
}

export function ExecutionBlock({ message }: ExecutionBlockProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const color = colorFor(message.category);
  const icon = STATUS_ICON[message.status ?? 'ok'] ?? '✓';
  const latency =
    message.latencyMs != null ? ` · ${message.latencyMs}ms` : '';

  return (
    <div
      className="my-1 rounded-lg font-mono text-2xs"
      style={{
        backgroundColor: '#fafaf9',
        border: '1px solid #f0efed',
        borderLeft: `3px solid ${color}`,
      }}
    >
      {/* 一行式 summary —— 默认显示；点击展开 details */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-[#ebebe9]"
        style={{ color: '#202124' }}
      >
        {message.status === 'running' ? (
          <span
            className="animate-spin-ring flex-shrink-0 rounded-full"
            style={{
              width: 11,
              height: 11,
              border: '2px solid #d1d5db',
              borderTopColor: color,
            }}
            title="running"
          />
        ) : (
          <span
            className="flex-shrink-0 font-bold"
            style={{ color, width: 14 }}
            title={message.status ?? 'ok'}
          >
            {icon}
          </span>
        )}
        <span
          className="flex-shrink-0 font-semibold uppercase tracking-wider"
          style={{ color }}
        >
          {message.category ?? 'step'}
        </span>
        <span className="flex-1 truncate">{message.content}</span>
        {latency && (
          <span className="flex-shrink-0" style={{ color: '#9ca3af' }}>
            {latency}
          </span>
        )}
        <span className="flex-shrink-0" style={{ color: '#9ca3af' }}>
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open && (
        <div
          className="border-t px-3 py-2"
          style={{ borderColor: '#f0efed', color: '#6b7280' }}
        >
          {/* 详情：完整 content + runId，方便用户复制 */}
          <pre className="whitespace-pre-wrap break-all font-mono text-[10px]">
            {message.content}
          </pre>
          {message.runId && (
            <div className="mt-1 text-[10px]" style={{ color: '#9ca3af' }}>
              run_id={message.runId}
            </div>
          )}
        </div>
      )}
    </div>
  );
}