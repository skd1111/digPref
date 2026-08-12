/**
 * DiffViewer —— Monaco Diff Editor 封装（审批详情用）。
 *
 * 设计要点：
 *   - 横向 Side-by-Side 显示 before/after
 *   - 自动检测 SQL / YAML / Java / Python 等语言
 *   - 头部显示 diff 摘要
 *
 * 性能红线（ROADMAP §12.3）：
 *   - Monaco 实例直接挂 ref，不进 React state
 *   - xterm-style 高频更新绕过 React（Monaco 自带虚拟滚动）
 */
import { useEffect, useRef } from 'react';
import { loader, type Monaco } from '@monaco-editor/react';

interface DiffViewerProps {
  before: string;
  after: string;
  language: string;
  summary?: string;
}

let monacoConfigured = false;

function ensureMonacoConfigured(monaco: Monaco): void {
  if (monacoConfigured) return;
  monaco.editor.defineTheme('audit-light', {
    base: 'vs',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#ffffff',
      'editor.foreground': '#1f1f1f',
      'editorGutter.background': '#f3f3f3',
      'diffEditor.insertedTextBackground': 'rgba(5, 150, 105, 0.18)',
      'diffEditor.removedTextBackground': 'rgba(205, 49, 49, 0.15)',
      'diffEditor.insertedLineBackground': 'rgba(5, 150, 105, 0.10)',
      'diffEditor.removedLineBackground': 'rgba(205, 49, 49, 0.08)',
    },
  });
  monacoConfigured = true;
}

export function DiffViewer({ before, after, language, summary }: DiffViewerProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  // 存实例（用 unknown 避免 monaco 类型路径问题）
  const diffEditorRef = useRef<unknown>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;

    loader.init().then((monaco) => {
      if (disposed || !containerRef.current) return;
      ensureMonacoConfigured(monaco);

      // 复用旧实例（不卸载，避免 Monaco 闪烁）
      if (!diffEditorRef.current) {
        const ed = monaco.editor.createDiffEditor(containerRef.current, {
          theme: 'audit-light',
          automaticLayout: true,
          readOnly: true,
          renderSideBySide: true,
          fontSize: 12,
          lineNumbers: 'on',
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          folding: true,
          renderIndicators: true,
          diffWordWrap: 'off',
          ignoreTrimWhitespace: false,
        });
        diffEditorRef.current = ed;
      }
      const ed = diffEditorRef.current as {
        getModel: () => {
          original: { setValue: (s: string) => void };
          modified: { setValue: (s: string) => void };
        } | null;
        dispose: () => void;
      };
      const model = ed.getModel();
      if (model) {
        model.original.setValue(before);
        model.modified.setValue(after);
        const langId = mapLanguage(language);
        monaco.editor.setModelLanguage(model.original, langId);
        monaco.editor.setModelLanguage(model.modified, langId);
      }
    });

    return () => {
      disposed = true;
    };
  }, [before, after, language]);

  // 卸载时销毁（防止内存泄漏）
  useEffect(() => {
    return () => {
      const ed = diffEditorRef.current as { dispose?: () => void } | null;
      if (ed && typeof ed.dispose === 'function') {
        ed.dispose();
        diffEditorRef.current = null;
      }
    };
  }, []);

  return (
    <div className="diff-viewer flex h-full flex-col" style={{ backgroundColor: '#ffffff' }}>
      {summary && (
        <div
          className="flex-shrink-0 border-b px-3 py-1.5 text-2xs"
          style={{ borderColor: '#d4d4d4', color: '#333333', backgroundColor: '#f3f3f3' }}
        >
          <span style={{ color: '#0451a5' }}>📝 变更摘要：</span> {summary}
        </div>
      )}
      <div ref={containerRef} className="flex-1" style={{ minHeight: 0 }} />
    </div>
  );
}

function mapLanguage(input: string): string {
  const lower = input.toLowerCase();
  if (lower === 'sql') return 'sql';
  if (lower === 'yaml' || lower === 'yml') return 'yaml';
  if (lower === 'json') return 'json';
  if (lower === 'java') return 'java';
  if (lower === 'python' || lower === 'py') return 'python';
  if (lower === 'typescript' || lower === 'ts') return 'typescript';
  if (lower === 'javascript' || lower === 'js') return 'javascript';
  if (lower === 'shell' || lower === 'bash' || lower === 'sh') return 'shell';
  if (lower === 'dockerfile') return 'dockerfile';
  return 'plaintext';
}
