/**
 * FileReferenceBadge —— 文件操作徽章（Phase 16）。
 *
 * 显示：文件名 + 操作类型 + 行范围 + +/- 行数统计。
 * 交互：
 *   - hover 200ms 防抖 → 显示 FileDiffTooltip（懒加载 diff 数据）
 *   - 点击 → 打开 FullDiffModal 完整对比视图
 */
import { useEffect, useRef, useState } from 'react';
import type { FileOperation } from '@eaide/shared-protocol';
import { ipc } from '@/ipc/invoke';
import { baseName } from './fileRef';
import { FileDiffTooltip } from './FileDiffTooltip';
import { FullDiffModal } from './FullDiffModal';

/** hover 防抖延迟（架构师忠告 4：200ms） */
const HOVER_DEBOUNCE_MS = 200;

const OP_LABEL: Record<string, string> = {
  read: '读取',
  write: '写入',
  edit: '编辑',
  grep: '搜索',
  reference: '引用',
};

const OP_COLOR: Record<string, string> = {
  read: '#059669',
  write: '#0451a5',
  edit: '#795e26',
  grep: '#616161',
  reference: '#616161',
};

export function FileReferenceBadge({
  op,
  stepId,
  fileIndex,
}: {
  op: FileOperation;
  stepId: string;
  fileIndex: number;
}): JSX.Element {
  const [hover, setHover] = useState(false);
  const [tooltipVisible, setTooltipVisible] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const [modalOpen, setModalOpen] = useState(false);
  /** 懒加载后的完整 op（可能补齐 diff） */
  const [loadedOp, setLoadedOp] = useState<FileOperation>(op);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const badgeRef = useRef<HTMLButtonElement>(null);

  // 卸载清理定时器
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleEnter = (): void => {
    setHover(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      const rect = badgeRef.current?.getBoundingClientRect();
      if (rect) {
        // 固定定位：徽章下方，避免超出右边界
        const left = Math.max(8, Math.min(rect.left, window.innerWidth - 500));
        const top = Math.min(rect.bottom + 6, window.innerHeight - 360);
        setPos({ top, left });
      }
      // 懒加载：只在 hover 时请求 diff 数据（架构师忠告 4）
      if (!loadedOp.diff && !loadedOp.preview && (op.type === 'write' || op.type === 'edit')) {
        void ipc
          .traceGetFileDiff(stepId, fileIndex)
          .then((resp) => {
            setLoadedOp((prev) => ({
              ...prev,
              diff: resp.diff || prev.diff,
              preview: resp.preview || prev.preview,
              lines_added: resp.lines_added,
              lines_removed: resp.lines_removed,
            }));
          })
          .catch(() => {
            /* Agent 未就绪 → 静默 */
          });
      }
      setTooltipVisible(true);
    }, HOVER_DEBOUNCE_MS);
  };

  const handleLeave = (): void => {
    setHover(false);
    if (timerRef.current) clearTimeout(timerRef.current);
    setTooltipVisible(false);
  };

  const color = OP_COLOR[op.type] ?? '#616161';
  const lineRange =
    op.start_line != null ? ` · L${op.start_line}${op.end_line != null ? `-${op.end_line}` : ''}` : '';
  const hasDiff = op.type === 'write' || op.type === 'edit';

  return (
    <>
      <button
        ref={badgeRef}
        type="button"
        onMouseEnter={handleEnter}
        onMouseLeave={handleLeave}
        onClick={() => hasDiff && setModalOpen(true)}
        className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-2xs"
        style={{
          borderColor: '#d0d0d0',
          backgroundColor: hover && hasDiff ? '#eef6ff' : '#f7f7f7',
          color: '#1f1f1f',
          cursor: hasDiff ? 'pointer' : 'default',
        }}
        title={op.path}
      >
        <span style={{ color }}>📄</span>
        <span className="font-semibold" style={{ color }}>
          {OP_LABEL[op.type] ?? op.type}
        </span>
        <span className="font-mono">{baseName(op.path)}</span>
        <span style={{ color: '#616161' }}>{lineRange}</span>
        {hasDiff && (
          <span className="font-mono">
            <span style={{ color: '#059669' }}>+{loadedOp.lines_added}</span>
            <span style={{ color: '#cd3131' }}>-{loadedOp.lines_removed}</span>
          </span>
        )}
        {!op.ok && <span style={{ color: '#cd3131' }}>✗</span>}
      </button>

      {tooltipVisible && <FileDiffTooltip op={loadedOp} pos={pos} />}
      {modalOpen && (
        <FullDiffModal op={loadedOp} onClose={() => setModalOpen(false)} />
      )}
    </>
  );
}
