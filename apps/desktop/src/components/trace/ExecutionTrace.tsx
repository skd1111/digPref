/**
 * ExecutionTrace — vertical timeline of LangGraph node transitions.
 *
 * Visual features:
 *   - Per-node icon (intent / planner / tool_runner / hitl_gate / repair / responder)
 *   - Duration heat bar (proportional width, color-coded)
 *   - Error path highlighted in red
 *   - HITL gate shows approval_id + decision chip
 *   - Auto-collapse errors when there are too many (>20)
 */
import { useTraceStore } from '@/store/traceStore';
import { isMockText } from '@/lib/mockFilter';
import { TraceNode } from './TraceNode';

export function ExecutionTrace(): JSX.Element {
  // 渲染层兜底：执行链路同样不显示任何 mock 数据
  const steps = useTraceStore((s) => s.steps).filter(
    (s) => !isMockText(s.summary) && !isMockText(s.error),
  );
  if (steps.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-3 text-center text-xs text-fg-dim">
        尚无执行链路 — 输入一条指令开始
      </div>
    );
  }
  return (
    <ol className="space-y-1 p-2 text-xs">
      {steps.map((s, i) => (
        <TraceNode key={s.id ?? `${s.node}-${i}`} index={i} step={s} />
      ))}
    </ol>
  );
}
