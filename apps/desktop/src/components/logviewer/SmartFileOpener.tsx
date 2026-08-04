/**
 * SmartFileOpener —— Phase 2F+ V1.5
 *
 * 根据文件大小自动选择渲染器：
 *   - < 50 MB → Monaco Editor（代码查看）
 *   - >= 50 MB → LogViewer（虚拟滚动大文件查看器）
 *
 * 同时也提供一个手动切换按钮（"Switch to LogViewer" / "Switch to Editor"）。
 *
 * 文件大小通过 `logviewer_stat_file` Tauri command 获取（Rust 端做 path
 * canonicalization + directory-rejection，前端不做 stat 直调）。
 */

import { useState, useEffect } from 'react';
import { ipc } from '@/ipc/invoke';
import { LogViewer } from './LogViewer';

/** 自动切换阈值：50 MB */
const AUTO_SWITCH_THRESHOLD = 50 * 1024 * 1024;

export interface SmartFileOpenerProps {
  /** 文件路径 */
  filePath: string;
  /** Monaco Editor 渲染回调 */
  renderEditor?: (filePath: string) => React.ReactNode;
  /** 关闭回调（返回到文件列表） */
  onClose?: () => void;
}

export type ViewerMode = 'auto' | 'editor' | 'logviewer';

/** 文件 stat 信息 */
interface FileStat {
  size: number;
  modified_secs: number;
}

export function SmartFileOpener({
  filePath,
  renderEditor,
  onClose,
}: SmartFileOpenerProps): JSX.Element {
  const [mode, setMode] = useState<ViewerMode>('auto');
  const [fileStat, setFileStat] = useState<FileStat | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function check(): Promise<void> {
      setLoading(true);
      setError(null);
      try {
        const stat = await ipc.logviewerStatFile(filePath) as FileStat;
        if (!cancelled) {
          setFileStat(stat);
          // 自动决定 mode
          if (stat.size >= AUTO_SWITCH_THRESHOLD) {
            setMode('logviewer');
          } else {
            setMode('editor');
          }
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    check();
    return () => { cancelled = true; };
  }, [filePath]);

  const effectiveMode = mode === 'auto'
    ? (fileStat && fileStat.size >= AUTO_SWITCH_THRESHOLD ? 'logviewer' : 'editor')
    : mode;

  const sizeMB = fileStat ? (fileStat.size / (1024 * 1024)).toFixed(1) : '?';

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        <span className="animate-pulse">Checking file size...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-500 text-sm">
        <div className="text-center">
          <p className="mb-2">Failed to open: {filePath.split(/[/\\]/).pop()}</p>
          <p className="text-xs text-gray-400">{error}</p>
          {onClose && (
            <button
              className="mt-3 text-xs px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded hover:bg-gray-300"
              onClick={onClose}
            >
              Back
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Mode bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span className="font-medium text-gray-700 dark:text-gray-300">
            {filePath.split(/[/\\]/).pop()}
          </span>
          <span>{sizeMB} MB</span>
          <span>
            {effectiveMode === 'logviewer' ? '📊 Log Viewer' : '📝 Editor'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* Manual override */}
          <button
            className="text-xs px-2 py-0.5 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700"
            onClick={() => setMode(effectiveMode === 'logviewer' ? 'editor' : 'logviewer')}
          >
            Switch to {effectiveMode === 'logviewer' ? 'Editor' : 'Log Viewer'}
          </button>
          {onClose && (
            <button
              className="text-xs px-2 py-0.5 rounded text-gray-400 hover:text-gray-600"
              onClick={onClose}
            >
              ✕ Close
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {effectiveMode === 'logviewer' ? (
          onClose
            ? <LogViewerInline filePath={filePath} onClose={onClose} />
            : <LogViewerInline filePath={filePath} />
        ) : renderEditor ? (
          renderEditor(filePath)
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            Editor not available — open in Log Viewer instead
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Inline LogViewer wrapper for SmartFileOpener.
 */
function LogViewerInline({ filePath, onClose }: { filePath: string; onClose?: () => void }): JSX.Element {
  if (onClose) {
    return <LogViewer filePath={filePath} onClose={onClose} />;
  }
  return <LogViewer filePath={filePath} />;
}
