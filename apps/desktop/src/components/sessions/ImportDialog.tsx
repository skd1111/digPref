/**
 * ImportDialog —— Phase 6 V1.5 加密 .eas 导入弹窗。
 *
 * 配置：.eas 文件路径 + 是否作为分支导入 + 父会话 ID（分支模式可选）。
 * 导入成功后展示新会话 ID / 消息数 / 哈希链校验结果。
 */
import { useState } from 'react';
import { useSessionsStore } from '@/store/sessionsStore';

interface Props {
  open: boolean;
  onClose: () => void;
  /** 导入成功后的回调（传新会话 ID） */
  onImported?: (newSessionId: string) => void;
}

interface ImportResult {
  new_session_id: string;
  message_count?: number;
  checksum?: string;
  chain_check?: { valid?: boolean; broken_reason?: string | null };
}

export function ImportDialog({ open, onClose, onImported }: Props): JSX.Element | null {
  const importSession = useSessionsStore((s) => s.importSession);
  const [easPath, setEasPath] = useState('');
  const [asBranch, setAsBranch] = useState(false);
  const [parentSessionId, setParentSessionId] = useState('');
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const handleImport = async (): Promise<void> => {
    if (!easPath.trim()) {
      setError('请填写 .eas 文件路径');
      return;
    }
    if (asBranch && !parentSessionId.trim()) {
      setError('分支导入需要填写父会话 ID');
      return;
    }
    setError(null);
    setResult(null);
    setBusy(true);
    const r = await importSession({
      eas_path: easPath.trim(),
      import_as_branch: asBranch,
      parent_session_id: asBranch ? parentSessionId.trim() : null,
    });
    setBusy(false);
    if (r) {
      setResult(r as unknown as ImportResult);
      onImported?.(r.new_session_id);
    } else {
      setError('导入失败（文件不存在 / 校验失败 / Keyring 密钥缺失）');
    }
  };

  const handleClose = (): void => {
    setEasPath('');
    setAsBranch(false);
    setParentSessionId('');
    setResult(null);
    setError(null);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}
      onClick={handleClose}
    >
      <div
        className="w-[500px] rounded p-4 shadow-xl"
        style={{ backgroundColor: '#ffffff', color: '#333333' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-3 text-lg font-semibold">📥 导入会话（.eas）</h2>
        <div className="space-y-3 text-sm">
          <div>
            <label className="mb-1 block text-xs" style={{ color: '#616161' }}>
              .eas 文件路径 *
            </label>
            <input
              type="text"
              value={easPath}
              onChange={(e) => setEasPath(e.target.value)}
              placeholder="C:\Users\xxx\backup.eas"
              className="w-full rounded px-2 py-1 font-mono text-sm"
              style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #c0c0c0' }}
              autoFocus
            />
          </div>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={asBranch}
              onChange={(e) => setAsBranch(e.target.checked)}
            />
            <span>作为分支导入（挂到现有会话下）</span>
          </label>
          {asBranch && (
            <div>
              <label className="mb-1 block text-xs" style={{ color: '#616161' }}>
                父会话 ID *
              </label>
              <input
                type="text"
                value={parentSessionId}
                onChange={(e) => setParentSessionId(e.target.value)}
                placeholder="父会话 UUID"
                className="w-full rounded px-2 py-1 font-mono text-sm"
                style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #c0c0c0' }}
              />
            </div>
          )}
          {result && (
            <div
              className="rounded p-2 text-xs"
              style={{ backgroundColor: '#e6f4e6', color: '#1e6b1e' }}
            >
              ✓ 导入成功
              <div className="mt-1 font-mono">新会话 ID：{result.new_session_id.slice(0, 24)}…</div>
              {typeof result.message_count === 'number' && <div>消息数：{result.message_count}</div>}
              {result.chain_check && (
                <div>
                  哈希链：{result.chain_check.valid ? '✓ 完整' : `⚠️ ${result.chain_check.broken_reason ?? '损坏'}`}
                </div>
              )}
            </div>
          )}
          {error && (
            <div className="text-xs" style={{ color: '#cd3131' }}>
              ⚠️ {error}
            </div>
          )}
          <div className="flex gap-2 pt-2">
            <button
              type="button"
              onClick={() => void handleImport()}
              disabled={busy}
              className="flex-1 rounded px-3 py-1.5 text-sm disabled:opacity-60"
              style={{ backgroundColor: '#0e639c', color: '#fff' }}
            >
              {busy ? '导入中…' : '导入'}
            </button>
            <button
              type="button"
              onClick={handleClose}
              className="rounded px-3 py-1.5 text-sm hover:bg-[#333]"
            >
              关闭
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
