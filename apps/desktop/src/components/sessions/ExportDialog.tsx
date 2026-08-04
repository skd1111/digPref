/**
 * ExportDialog —— Phase 6 V1.5 加密 .eas 导出弹窗。
 *
 * 配置：输出路径 + 是否包含消息 + 是否包含事件链 + 是否 PII 脱敏。
 */
import { useState } from 'react';
import { useSessionsStore } from '@/store/sessionsStore';

interface Props {
  sessionId: string;
  open: boolean;
  onClose: () => void;
}

export function ExportDialog({ sessionId, open, onClose }: Props): JSX.Element | null {
  const exportSession = useSessionsStore((s) => s.exportSession);
  const [outputPath, setOutputPath] = useState('');
  const [includeMessages, setIncludeMessages] = useState(true);
  const [includeEventChain, setIncludeEventChain] = useState(true);
  const [scrubPii, setScrubPii] = useState(true);
  const [result, setResult] = useState<{ path: string; bytes: number; checksum: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const handleExport = async () => {
    if (!outputPath.trim()) {
      setError('请填写输出路径');
      return;
    }
    setError(null);
    setResult(null);
    const r = await exportSession(sessionId, {
      output_path: outputPath.trim(),
      include_messages: includeMessages,
      include_event_chain: includeEventChain,
      scrub_pii: scrubPii,
    });
    if (r) {
      setResult({ path: r.path, bytes: r.bytes, checksum: r.checksum });
    } else {
      setError('导出失败');
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}
      onClick={onClose}
    >
      <div
        className="w-[500px] rounded p-4 shadow-xl"
        style={{ backgroundColor: '#ffffff', color: '#333333' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-3 text-lg font-semibold">💾 加密导出（.eas）</h2>
        <div className="space-y-3 text-sm">
          <div>
            <label className="mb-1 block text-xs" style={{ color: '#616161' }}>
              输出文件路径 *
            </label>
            <input
              type="text"
              value={outputPath}
              onChange={(e) => setOutputPath(e.target.value)}
              placeholder="C:\\Users\\xxx\\backup.eas"
              className="w-full rounded px-2 py-1 text-sm font-mono"
              style={{ backgroundColor: '#ececec', color: '#fff', border: '1px solid #c0c0c0' }}
              autoFocus
            />
          </div>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={includeMessages}
              onChange={(e) => setIncludeMessages(e.target.checked)}
            />
            <span>包含消息体</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={includeEventChain}
              onChange={(e) => setIncludeEventChain(e.target.checked)}
            />
            <span>包含事件哈希链</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={scrubPii}
              onChange={(e) => setScrubPii(e.target.checked)}
            />
            <span>
              PII 脱敏
              <span className="ml-1 text-xs" style={{ color: '#616161' }}>
                （手机/身份证/银行卡/AWS Key/JWT/IPv4/邮箱/高熵 token）
              </span>
            </span>
          </label>
          {result && (
            <div
              className="rounded p-2 text-xs"
              style={{ backgroundColor: '#0e3a0e', color: '#6a9955' }}
            >
              ✓ 导出成功
              <div className="mt-1 font-mono">{result.path}</div>
              <div>大小：{(result.bytes / 1024).toFixed(1)} KB</div>
              <div className="font-mono">SHA-256: {result.checksum.slice(0, 16)}…</div>
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
              onClick={handleExport}
              className="flex-1 rounded px-3 py-1.5 text-sm"
              style={{ backgroundColor: '#0e639c', color: '#fff' }}
            >
              加密导出
            </button>
            <button
              type="button"
              onClick={onClose}
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