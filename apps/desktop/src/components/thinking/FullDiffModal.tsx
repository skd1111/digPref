/**
 * FullDiffModal —— 完整 diff 对比视图（Phase 16）。
 *
 * 点击 FileReferenceBadge 打开；Monaco 只读渲染 unified diff。
 * Esc 或点击遮罩关闭。
 */
import { useEffect } from 'react';
import Editor from '@monaco-editor/react';
import type { FileOperation } from '@eaide/shared-protocol';
import { baseName } from './fileRef';

const OP_LABEL: Record<string, string> = {
  read: '读取',
  write: '写入',
  edit: '编辑',
  grep: '搜索',
  reference: '引用',
};

export function FullDiffModal({
  op,
  onClose,
}: {
  op: FileOperation;
  onClose: () => void;
}): JSX.Element {
  // Esc 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const diff = op.diff ?? op.preview ?? '';

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.45)' }}
      onClick={onClose}
      role="presentation"
    >
      <div
        className="flex h-[80vh] w-[70vw] flex-col overflow-hidden rounded shadow-xl"
        style={{ backgroundColor: '#ffffff' }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <header
          className="flex flex-shrink-0 items-center justify-between border-b px-4 py-2"
          style={{ borderColor: '#e0e0e0', backgroundColor: '#f3f3f3' }}
        >
          <div className="flex items-center gap-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
            <span>📄 {baseName(op.path)}</span>
            <span
              className="rounded px-1.5 py-0.5 text-2xs"
              style={{ backgroundColor: '#e0e0e0', color: '#333333' }}
            >
              {OP_LABEL[op.type] ?? op.type}
            </span>
            <span className="text-2xs font-mono">
              <span style={{ color: '#059669' }}>+{op.lines_added}</span>
              {'  '}
              <span style={{ color: '#cd3131' }}>-{op.lines_removed}</span>
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-0.5 text-ui"
            style={{ color: '#616161', cursor: 'pointer' }}
            title="关闭（Esc）"
          >
            ✕
          </button>
        </header>

        <div className="flex-shrink-0 border-b px-4 py-1 text-2xs font-mono" style={{ borderColor: '#e0e0e0', color: '#616161' }}>
          {op.path}
        </div>

        <div className="min-h-0 flex-1">
          {diff === '' ? (
            <div className="flex h-full items-center justify-center text-ui" style={{ color: '#616161' }}>
              {op.error ?? '该操作没有内容变更（只读操作）'}
            </div>
          ) : (
            <Editor
              value={diff}
              language="diff"
              theme="light"
              options={{
                readOnly: true,
                minimap: { enabled: false },
                fontSize: 12,
                lineNumbers: 'on',
                scrollBeyondLastLine: false,
                wordWrap: 'on',
                domReadOnly: true,
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
