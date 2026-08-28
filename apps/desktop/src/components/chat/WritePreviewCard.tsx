/**
 * WritePreviewCard —— 写前 Diff 预览卡（执行过程可视化 · 阶段四）。
 *
 * 写类工具（write_file / edit_file）在 HITL 审批暂停前先下发
 * file_write_preview 事件（unified diff），diff 存 chatStore.writePreviewByCall。
 * 本卡片挂在对应预览消息下：
 *   - 内嵌 +/- 行数统计（一眼看清改动规模）；
 *   - 「查看完整 Diff」→ 复用思维链的 FullDiffModal（Monaco 红绿对比）。
 * 审批卡（ApprovalCard）紧随其后出现 —— 用户看清「将改什么」再做批准决定。
 */
import { useState } from 'react';
import type { FileOperation } from '@eaide/shared-protocol';
import { useChatStore } from '@/store/chatStore';
import { FullDiffModal } from '../thinking/FullDiffModal';

interface WritePreviewCardProps {
  callId: string;
}

/** 统计 unified diff 的 + / - 行数（跳过头部 +++ / --- 文件行） */
function diffStats(diff: string): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of diff.split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) added += 1;
    else if (line.startsWith('-') && !line.startsWith('---')) removed += 1;
  }
  return { added, removed };
}

export function WritePreviewCard({ callId }: WritePreviewCardProps): JSX.Element | null {
  const preview = useChatStore((s) => s.writePreviewByCall[callId]);
  const [modalOpen, setModalOpen] = useState(false);
  if (!preview || !preview.diff) return null;

  const { added, removed } = diffStats(preview.diff);
  const op: FileOperation = {
    type: 'edit',
    path: preview.path,
    diff: preview.diff,
    preview: null,
    lines_added: added,
    lines_removed: removed,
    start_line: null,
    end_line: null,
    ok: true,
    error: null,
  };

  return (
    <div
      className="mx-3 mb-2 flex items-center gap-2 rounded-md px-2 py-1.5 text-[10px]"
      style={{ backgroundColor: '#f8fafc', border: '1px solid #e2e8f0' }}
    >
      <span style={{ color: '#6b7280' }}>写前预览</span>
      <span className="font-mono" style={{ color: '#059669' }}>
        +{added}
      </span>
      <span className="font-mono" style={{ color: '#cd3131' }}>
        -{removed}
      </span>
      <button
        type="button"
        onClick={() => setModalOpen(true)}
        className="rounded px-1.5 py-0.5 font-medium transition-opacity hover:opacity-70"
        style={{ backgroundColor: '#eff6ff', color: '#2563eb' }}
      >
        查看完整 Diff
      </button>
      {modalOpen && <FullDiffModal op={op} onClose={() => setModalOpen(false)} />}
    </div>
  );
}
