/**
 * ThinkingStepCard —— 思维链单步卡片（Phase 16）。
 *
 * 展开/折叠；思考内容按【思考】/【行动】/【观察】/【决策】四段着色渲染，
 * 📄 文件引用自动高亮；工具调用与文件操作分别列表展示。
 */
import { useState } from 'react';
import type { ThinkingStep } from '@eaide/shared-protocol';
import { FileReferenceBadge } from './FileReferenceBadge';
import { splitFileRefs } from './fileRef';

const NODE_META: Record<string, { label: string; icon: string; color: string }> = {
  intent: { label: '意图识别', icon: '🧭', color: '#059669' },
  planner: { label: '任务规划', icon: '🧠', color: '#0451a5' },
  decompose: { label: '任务分解', icon: '🧩', color: '#c586c0' },
  tool_orchestrator: { label: '工具调用', icon: '🔧', color: '#0b6bcb' },
  tool_runner: { label: '工具执行', icon: '⚙️', color: '#0b6bcb' },
  hitl_gate: { label: '人工审批', icon: '🛡️', color: '#cd3131' },
  repair: { label: '自动修复', icon: '🩹', color: '#795e26' },
  responder: { label: '回答生成', icon: '💬', color: '#c586c0' },
  rag_retrieve: { label: '知识检索', icon: '📚', color: '#059669' },
  vision_understand: { label: '截图理解', icon: '👁️', color: '#059669' },
  local_intent: { label: '端侧意图', icon: '🧭', color: '#059669' },
  builtin_tool: { label: '内置工具', icon: '🔧', color: '#0b6bcb' },
};

/** 四段式标记着色 */
const SECTION_COLOR: Record<string, string> = {
  '【思考】': '#0451a5',
  '【行动】': '#0b6bcb',
  '【观察】': '#059669',
  '【决策】': '#795e26',
};

export function ThinkingStepCard({
  step,
  flash,
}: {
  step: ThinkingStep;
  /** 执行块跳转命中时闪烁提示（#153） */
  flash?: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(true);
  const meta = NODE_META[step.node_name] ?? {
    label: step.node_name,
    icon: '•',
    color: '#616161',
  };
  const ts = step.created_at ? new Date(step.created_at).toLocaleTimeString() : '';
  const latency = step.latency_ms != null ? `${step.latency_ms}ms` : '';

  return (
    <li id={`think-step-${step.id}`} className="relative pl-5">
      {/* 时间线竖点 */}
      <span
        className="absolute left-0 top-2 h-2.5 w-2.5 rounded-full"
        style={{ backgroundColor: meta.color, boxShadow: `0 0 0 3px ${meta.color}22` }}
      />
      <div
        className={`rounded border${flash ? ' trace-flash' : ''}`}
        style={{ borderColor: flash ? '#10a37f' : '#e0e0e0', backgroundColor: '#ffffff' }}
      >
        {/* 头部：点击折叠 */}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-2xs"
          style={{ cursor: 'pointer' }}
        >
          <span aria-hidden>{meta.icon}</span>
          <span className="font-semibold" style={{ color: meta.color }}>
            {meta.label}
          </span>
          <span className="flex-1 truncate" style={{ color: '#616161' }}>
            {oneLineSummary(step)}
          </span>
          {latency && (
            <span className="flex-shrink-0 font-mono" style={{ color: '#616161' }}>
              {latency}
            </span>
          )}
          <span className="flex-shrink-0" style={{ color: '#616161' }}>
            {ts} {open ? '▾' : '▸'}
          </span>
        </button>

        {/* 展开区 */}
        {open && (
          <div className="space-y-1.5 border-t px-2.5 py-2" style={{ borderColor: '#f0f0f0' }}>
            {step.thinking && <ThinkingText text={step.thinking} />}

            {step.tool_calls.length > 0 && (
              <div className="space-y-1">
                {step.tool_calls.map((tc, i) => (
                  <div
                    key={i}
                    className="rounded px-2 py-1 font-mono text-2xs"
                    style={{ backgroundColor: '#f7f7f7', color: '#1f1f1f' }}
                  >
                    <span style={{ color: '#0b6bcb' }}>🔧 {tc.name}</span>
                    {tc.risk_level && (
                      <span className="ml-2" style={{ color: riskColor(tc.risk_level) }}>
                        风险:{tc.risk_level}
                      </span>
                    )}
                    {tc.result && (
                      <span className="ml-2" style={{ color: tc.result.ok ? '#059669' : '#cd3131' }}>
                        {tc.result.ok ? '✓ 成功' : `✗ ${tc.result.error ?? '失败'}`}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {step.file_operations.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {step.file_operations.map((op, i) => (
                  <FileReferenceBadge
                    key={`${step.id}-op-${i}`}
                    op={op}
                    stepId={step.id}
                    fileIndex={i}
                  />
                ))}
              </div>
            )}

            {step.decision && !step.thinking?.includes('【决策】') && (
              <div className="text-2xs" style={{ color: '#795e26' }}>
                【决策】{step.decision}
              </div>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

/** 思考文本渲染：四段式标记着色 + 📄 文件引用高亮 */
function ThinkingText({ text }: { text: string }): JSX.Element {
  return (
    <div className="space-y-1 text-2xs leading-5" style={{ color: '#1f1f1f' }}>
      {text.split('\n').map((line, i) => (
        <LineWithRefs key={i} line={line} />
      ))}
    </div>
  );
}

function LineWithRefs({ line }: { line: string }): JSX.Element {
  const sectionKey = Object.keys(SECTION_COLOR).find((k) => line.startsWith(k));
  const color = sectionKey ? SECTION_COLOR[sectionKey] : undefined;
  const segments = splitFileRefs(line);
  return (
    <div style={{ color }}>
      {segments.map((seg, i) =>
        seg.file ? (
          <span
            key={i}
            className="rounded px-0.5 font-mono"
            style={{ backgroundColor: '#eef6ff', color: '#0451a5' }}
            title={seg.file}
          >
            {seg.text}
          </span>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </div>
  );
}

function oneLineSummary(step: ThinkingStep): string {
  if (step.decision) return step.decision.replace(/\s+/g, ' ');
  if (step.thinking) {
    const first = step.thinking.split('\n')[0] ?? '';
    return first.replace(/【(思考|行动|观察|决策)】/, '').slice(0, 60);
  }
  const tc = step.tool_calls[0];
  if (tc) return `调用 ${tc.name}`;
  const op = step.file_operations[0];
  if (op) return `文件操作 ${op.path}`;
  return '';
}

function riskColor(level: string): string {
  switch (level) {
    case 'read':
      return '#059669';
    case 'low':
      return '#0451a5';
    case 'medium':
      return '#795e26';
    case 'high':
    case 'critical':
      return '#cd3131';
    default:
      return '#616161';
  }
}
