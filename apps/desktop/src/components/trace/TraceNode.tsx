/**
 * TraceNode — single row in the execution trace timeline.
 *
 * Visual treatment:
 *   - Icon: per-node semantic glyph (intent = 🔍, planner = 📋, tool_runner = 🔧, etc.)
 *   - Duration bar: proportional width = (duration_ms / max_duration) × 100%,
 *     coloured by status (green = ok, red = fail, yellow = running)
 *   - Error path: red border + expandable error detail
 *   - HITL gate: chip with approval_id (truncated) + decision badge
 */
import { useState } from 'react';
import type { TraceStep } from '@eaide/shared-protocol';
import { fmtDuration } from '@/lib/format';

interface Props {
  step: TraceStep;
  index: number;
}

// Visual treatment per node
const NODE_META: Record<string, { icon: string; label: string; tone: string }> = {
  intent:      { icon: '🔍', label: 'Intent',     tone: 'text-accent' },
  planner:     { icon: '📋', label: 'Plan',       tone: 'text-accent' },
  tool_runner: { icon: '🔧', label: 'Tool',       tone: 'text-fg' },
  hitl_gate:   { icon: '✋', label: 'HITL',       tone: 'text-accent-warn' },
  repair:      { icon: '🛠',  label: 'Repair',     tone: 'text-accent-warn' },
  responder:   { icon: '💬', label: 'Answer',     tone: 'text-accent-approval' },
};

const STATUS_TONE: Record<string, string> = {
  ok:       'text-accent-approval',
  fail:     'text-accent-danger',
  running:  'text-accent-warn',
  skipped:  'text-fg-dim',
};

export function TraceNode({ step, index }: Props): JSX.Element {
  const meta = NODE_META[step.node] ?? {
    icon: '•', label: step.node, tone: 'text-fg',
  };
  const statusTone = STATUS_TONE[step.status] ?? 'text-fg-muted';
  const [expanded, setExpanded] = useState(false);
  const hasError = step.status === 'fail' || step.error;
  const dur = step.durationMs ?? 0;

  return (
    <li
      className={`group relative rounded border bg-bg-code p-2 transition-colors ${
        hasError ? 'border-accent-danger/60' : 'border-border hover:border-fg-dim'
      }`}
    >
      {/* ---- Duration heat bar (decorative — sits behind text) ---- */}
      {dur > 0 && (
        <div
          className={`pointer-events-none absolute inset-y-0 left-0 rounded ${
            hasError
              ? 'bg-accent-danger/10'
              : step.status === 'ok'
                ? 'bg-accent-approval/10'
                : 'bg-accent-warn/10'
          }`}
          style={{ width: `${Math.min(100, Math.log10(dur + 1) * 25)}%` }}
          aria-hidden
        />
      )}

      {/* ---- Main row ---- */}
      <div className="relative flex items-center gap-2">
        <span className="font-mono text-fg-dim">#{index + 1}</span>
        <span className="text-base" title={meta.label}>{meta.icon}</span>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className={`font-mono text-sm ${meta.tone}`}>{meta.label}</span>
            {step.approvalId && (
              <span className="rounded bg-bg-subtle px-1 font-mono text-[10px] text-fg-muted">
                {step.approvalId.slice(0, 12)}…
              </span>
            )}
            {step.decision && (
              <span className={`rounded px-1 text-[10px] font-semibold ${
                step.decision === 'approve'
                  ? 'bg-accent-approval/20 text-accent-approval'
                  : 'bg-accent-danger/20 text-accent-danger'
              }`}>
                {step.decision}
              </span>
            )}
          </div>
          {step.summary && (
            <div className="truncate text-fg-muted">{step.summary}</div>
          )}
        </div>
        <div className="flex flex-col items-end gap-0.5">
          <span className={`font-mono text-[11px] ${statusTone}`}>
            {step.status === 'ok' ? '✓' : step.status === 'fail' ? '✗' : '…'}
            {' '}{fmtDuration(dur)}
          </span>
          {step.attempts && step.attempts > 1 && (
            <span className="text-[10px] text-accent-warn">
              尝试 ×{step.attempts}
            </span>
          )}
        </div>
        {(hasError || step.rationale) && (
          <button
            onClick={() => setExpanded((x) => !x)}
            className="ml-1 text-fg-dim hover:text-fg"
            aria-label={expanded ? '收起' : '展开'}
          >
            {expanded ? '▾' : '▸'}
          </button>
        )}
      </div>

      {/* ---- Expanded detail ---- */}
      {expanded && (
        <div className="relative mt-2 space-y-1 border-t border-border pt-2 text-[11px]">
          {step.error && (
            <div className="rounded bg-accent-danger/10 px-2 py-1 font-mono text-accent-danger">
              {step.error}
            </div>
          )}
          {step.rationale && (
            <div className="text-fg-muted">
              <span className="text-fg-dim">rationale:</span> {step.rationale}
            </div>
          )}
          {step.toolName && (
            <div className="text-fg-muted">
              <span className="text-fg-dim">tool:</span> <code>{step.toolName}</code>
            </div>
          )}
        </div>
      )}
    </li>
  );
}