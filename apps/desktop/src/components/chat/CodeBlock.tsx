/**
 * CodeBlock — VSCode 风格代码块。
 *
 * 设计：
 *   - 顶部 header bar：左侧显示语言，右侧 "复制" 按钮
 *   - Monaco 编辑器：只读、行号、vs-light 主题、与全局一致的字体
 *   - 高度自适应（min 80px / max 400px）
 *   - 鼠标悬停 header 时"复制"按钮才出现
 */
import { useState } from 'react';
import Editor from '@monaco-editor/react';

type Language = 'sql' | 'json' | 'python' | 'bash' | 'typescript' | 'markdown';

const LANG_LABEL: Record<Language, string> = {
  sql: 'SQL',
  json: 'JSON',
  python: 'Python',
  bash: 'Bash',
  typescript: 'TypeScript',
  markdown: 'Markdown',
};

interface Props {
  code: string;
  language: Language | string;
}

export function CodeBlock({ code, language }: Props): JSX.Element {
  const [copied, setCopied] = useState(false);
  const langKey = (LANG_LABEL[language as Language] ? (language as Language) : 'plaintext');

  // 高度自适应（2026-08-07）：按行数撑开，min 80 / max 400，兑现头注释承诺
  const lineCount = code.split('\n').length;
  const editorHeight = Math.max(80, Math.min(400, lineCount * 18 + 24));

  const handleCopy = (): void => {
    void navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div
      className="my-2 overflow-hidden rounded-lg"
      style={{ border: '1px solid #e7e5e4', backgroundColor: '#ffffff' }}
    >
      {/* Header bar（aicss 风格，2026-08-10：尖括号图标 + 语言 + Copy 勾选态） */}
      <div
        className="group flex h-[28px] items-center justify-between border-b px-3 text-2xs"
        style={{
          backgroundColor: '#f5f5f4',
          borderColor: '#e7e5e4',
          color: '#6b7280',
        }}
      >
        <span className="flex items-center gap-1.5 uppercase tracking-wider">
          <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
            <path
              d="m8 6-6 6 6 6M16 6l6 6-6 6"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          {(LANG_LABEL as Record<string, string>)[langKey] ?? language.toUpperCase()}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1 rounded px-2 py-0.5 text-fg-muted opacity-0 transition-opacity hover:bg-vscode-border hover:text-fg group-hover:opacity-100"
          style={copied ? { opacity: 1, color: '#10a37f' } : undefined}
        >
          {copied ? (
            <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="m4.5 12.75 6 6 9-13.5" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="9" y="9" width="11" height="11" rx="2.5" />
              <path d="M5 15a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2" />
            </svg>
          )}
          {copied ? '已复制' : '复制'}
        </button>
      </div>

      {/* Monaco editor */}
      <Editor
        height={`${editorHeight}px`}
        {...(langKey !== 'plaintext' ? { language: langKey } : {})}
        value={code}
        theme="vs-light"
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 12,
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          lineNumbers: 'on',
          renderLineHighlight: 'none',
          renderWhitespace: 'none',
          folding: true,
          fontFamily: '"Cascadia Code", "JetBrains Mono", Consolas, monospace',
          padding: { top: 8, bottom: 8 },
        }}
      />
    </div>
  );
}
