/**
 * FileDiffTooltip —— 文件操作悬浮 diff 预览（Phase 16）。
 *
 * 性能策略（架构师忠告 4）：
 *   - hover 200ms 防抖后才挂载（由 FileReferenceBadge 控制）
 *   - 轻量着色渲染 unified diff（非 Monaco）——保证 100ms 内显示；
 *     完整对比视图（点击打开 FullDiffModal）才用 Monaco
 *   - 超长 diff 只渲染前 MAX_RENDER_LINES 行 + 截断提示
 */
import type { FileOperation } from '@eaide/shared-protocol';
import { baseName } from './fileRef';

const MAX_RENDER_LINES = 120;

interface TooltipPos {
  top: number;
  left: number;
}

export function FileDiffTooltip({
  op,
  pos,
}: {
  op: FileOperation;
  pos: TooltipPos;
}): JSX.Element {
  const diff = op.preview ?? op.diff ?? '';
  const lines = diff.split('\n');
  const truncated = lines.length > MAX_RENDER_LINES;
  const shown = truncated ? lines.slice(0, MAX_RENDER_LINES) : lines;

  return (
    <div
      className="pointer-events-none fixed z-[9999] w-[480px] max-w-[80vw] rounded shadow-lg"
      style={{
        top: pos.top,
        left: pos.left,
        backgroundColor: '#ffffff',
        border: '1px solid #d0d0d0',
      }}
    >
      <div
        className="flex items-center justify-between border-b px-3 py-1.5 text-2xs font-semibold"
        style={{ borderColor: '#e0e0e0', color: '#1f1f1f', backgroundColor: '#f3f3f3' }}
      >
        <span className="truncate">📄 {baseName(op.path)}</span>
        <span style={{ color: '#616161' }}>
          <span style={{ color: '#059669' }}>+{op.lines_added}</span>
          {'  '}
          <span style={{ color: '#cd3131' }}>-{op.lines_removed}</span>
        </span>
      </div>
      {diff === '' ? (
        <div className="px-3 py-2 text-2xs" style={{ color: '#616161' }}>
          {op.type === 'read' || op.type === 'grep'
            ? '只读操作，无内容变更'
            : op.error ?? '无 diff 数据'}
        </div>
      ) : (
        <pre
          className="overflow-hidden px-2 py-1 font-mono text-[11px] leading-4"
          style={{ maxHeight: 320 }}
        >
          {shown.map((line, i) => (
            <DiffLine key={i} line={line} />
          ))}
          {truncated && (
            <div className="py-0.5 text-center" style={{ color: '#616161' }}>
              … 已截断（共 {lines.length} 行），点击查看完整对比
            </div>
          )}
        </pre>
      )}
    </div>
  );
}

function DiffLine({ line }: { line: string }): JSX.Element {
  let color = '#1f1f1f';
  let bg = 'transparent';
  if (line.startsWith('+') && !line.startsWith('+++')) {
    color = '#05660d';
    bg = '#e6ffed';
  } else if (line.startsWith('-') && !line.startsWith('---')) {
    color = '#a01010';
    bg = '#ffeef0';
  } else if (line.startsWith('@@')) {
    color = '#0b6bcb';
  } else if (line.startsWith('---') || line.startsWith('+++')) {
    color = '#616161';
  }
  return (
    <div style={{ color, backgroundColor: bg, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
      {line || ' '}
    </div>
  );
}
