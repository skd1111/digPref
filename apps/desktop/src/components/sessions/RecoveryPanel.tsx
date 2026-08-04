/**
 * RecoveryPanel —— Phase 6 V1.5 启动恢复面板。
 *
 * 显示中断会话列表（updated_at 距今 > 阈值 + 有消息 + 非分支）。
 * 提供"恢复"按钮（点击打开会话）+ "忽略"按钮（仅关闭弹窗）。
 */
import { useEffect } from 'react';
import { useSessionsStore } from '@/store/sessionsStore';

interface Props {
  open: boolean;
  onClose: () => void;
  onResume?: (sessionId: string) => void;
}

export function RecoveryPanel({ open, onClose, onResume }: Props): JSX.Element | null {
  const loadRecovery = useSessionsStore((s) => s.loadRecovery);
  const recoveryReport = useSessionsStore((s) => s.recoveryReport);

  useEffect(() => {
    if (open) {
      void loadRecovery({ idle_threshold_ms: 300_000, limit: 50 });
    }
  }, [open, loadRecovery]);

  if (!open) return null;
  if (!recoveryReport) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center"
        style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      >
        <div
          className="rounded p-4"
          style={{ backgroundColor: '#ffffff', color: '#333333' }}
        >
          扫描中...
        </div>
      </div>
    );
  }

  if (!recoveryReport.needs_recovery || recoveryReport.total === 0) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center"
        style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
        onClick={onClose}
      >
        <div
          className="w-[400px] rounded p-4 shadow-xl"
          style={{ backgroundColor: '#ffffff', color: '#333333' }}
          onClick={(e) => e.stopPropagation()}
        >
          <h2 className="mb-2 text-lg font-semibold">✓ 无可恢复会话</h2>
          <p className="text-sm" style={{ color: '#616161' }}>
            所有会话都已正常结束，无需恢复。
          </p>
          <button
            type="button"
            onClick={onClose}
            className="mt-3 rounded px-3 py-1 text-sm"
            style={{ backgroundColor: '#0e639c', color: '#fff' }}
          >
            确定
          </button>
        </div>
      </div>
    );
  }

  const idleMinutes = Math.round(recoveryReport.oldest_idle_ms / 60_000);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
    >
      <div
        className="w-[500px] rounded shadow-xl"
        style={{ backgroundColor: '#ffffff', color: '#333333' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 py-3" style={{ borderBottom: '1px solid #c0c0c0' }}>
          <h2 className="text-lg font-semibold">⚠️ 检测到 {recoveryReport.total} 个中断会话</h2>
          <p className="mt-1 text-xs" style={{ color: '#616161' }}>
            空闲阈值：{Math.round(recoveryReport.threshold_ms / 60_000)} 分钟 · 最久空闲：{idleMinutes} 分钟
          </p>
        </div>
        <div className="max-h-[400px] overflow-y-auto p-4">
          {recoveryReport.resumable_ids.map((sid) => (
            <div
              key={sid}
              className="mb-2 rounded p-2"
              style={{ backgroundColor: '#f3f3f3', border: '1px solid #c0c0c0' }}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs">{sid.slice(0, 16)}…</span>
                <div className="flex gap-1">
                  {onResume && (
                    <button
                      type="button"
                      onClick={() => {
                        onResume(sid);
                        onClose();
                      }}
                      className="rounded px-2 py-1 text-xs"
                      style={{ backgroundColor: '#0e639c', color: '#fff' }}
                    >
                      恢复
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
        <div className="flex justify-end gap-2 px-4 py-3" style={{ borderTop: '1px solid #c0c0c0' }}>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-3 py-1 text-sm hover:bg-[#333]"
          >
            稍后处理
          </button>
        </div>
      </div>
    </div>
  );
}