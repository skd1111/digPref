/**
 * BranchDialog —— Phase 6 V1.5 创建分支会话弹窗。
 */
import { useState } from 'react';
import { useSessionsStore } from '@/store/sessionsStore';
import { GitForkIcon, WarningIcon } from '@/components/ui/Icons';

interface Props {
  sessionId: string;
  open: boolean;
  onClose: () => void;
}

export function BranchDialog({ sessionId, open, onClose }: Props): JSX.Element | null {
  const branchCreate = useSessionsStore((s) => s.branchCreate);
  const [branchLabel, setBranchLabel] = useState('');
  const [fromCheckpointId, setFromCheckpointId] = useState('');
  const [titleSuffix, setTitleSuffix] = useState(' (分支)');
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const handleCreate = async () => {
    if (!branchLabel.trim()) {
      setError('请输入分支标签');
      return;
    }
    setError(null);
    const trimmedCp = fromCheckpointId.trim();
    const branch = await branchCreate(sessionId, {
      branch_label: branchLabel.trim(),
      ...(trimmedCp && { from_checkpoint_id: trimmedCp }),
      title_suffix: titleSuffix,
    });
    if (branch) {
      setBranchLabel('');
      setFromCheckpointId('');
      onClose();
    } else {
      setError('创建失败');
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}
      onClick={onClose}
    >
      <div
        className="w-[400px] rounded p-4 shadow-xl"
        style={{ backgroundColor: '#ffffff', color: '#333333' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-3 flex items-center gap-1.5 text-lg font-semibold">
          <GitForkIcon size={17} />
          创建分支会话
        </h2>
        <div className="space-y-3 text-sm">
          <div>
            <label className="mb-1 block text-xs" style={{ color: '#616161' }}>
              分支标签 *
            </label>
            <input
              type="text"
              value={branchLabel}
              onChange={(e) => setBranchLabel(e.target.value)}
              placeholder="bugfix-order-amount"
              className="w-full rounded px-2 py-1 text-sm"
              style={{ backgroundColor: '#ececec', color: '#fff', border: '1px solid #c0c0c0' }}
              autoFocus
            />
          </div>
          <div>
            <label className="mb-1 block text-xs" style={{ color: '#616161' }}>
              从哪个 checkpoint 派生（可选）
            </label>
            <input
              type="text"
              value={fromCheckpointId}
              onChange={(e) => setFromCheckpointId(e.target.value)}
              placeholder="cp-uuid 留空=末尾"
              className="w-full rounded px-2 py-1 text-sm"
              style={{ backgroundColor: '#ececec', color: '#fff', border: '1px solid #c0c0c0' }}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs" style={{ color: '#616161' }}>
              标题后缀
            </label>
            <input
              type="text"
              value={titleSuffix}
              onChange={(e) => setTitleSuffix(e.target.value)}
              className="w-full rounded px-2 py-1 text-sm"
              style={{ backgroundColor: '#ececec', color: '#fff', border: '1px solid #c0c0c0' }}
            />
          </div>
          {error && (
            <div className="flex items-center gap-1 text-xs" style={{ color: '#cd3131' }}>
              <WarningIcon size={12} />
              {error}
            </div>
          )}
          <div className="flex gap-2 pt-2">
            <button
              type="button"
              onClick={handleCreate}
              className="btn btn-primary btn-md flex-1"
            >
              创建分支
            </button>
            <button
              type="button"
              onClick={onClose}
              className="btn btn-secondary btn-md"
            >
              取消
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}