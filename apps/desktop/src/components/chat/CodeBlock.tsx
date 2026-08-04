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

  const handleCopy = (): void => {
    void navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div
      className="my-2 overflow-hidden rounded"
      style={{ border: '1px solid #d4d4d4', backgroundColor: '#ffffff' }}
    >
      {/* Header bar */}
      <div
        className="group flex h-[28px] items-center justify-between border-b px-3 text-2xs uppercase tracking-wider"
        style={{
          backgroundColor: '#f3f3f3',
          borderColor: '#d4d4d4',
          color: '#333333',
        }}
      >
        <span>{(LANG_LABEL as Record<string, string>)[langKey] ?? language.toUpperCase()}</span>
        <button
          type="button"
          onClick={handleCopy}
          className="rounded px-2 py-0.5 text-fg-muted opacity-0 transition-opacity hover:bg-vscode-border hover:text-fg group-hover:opacity-100"
          style={copied ? { opacity: 1 } : undefined}
        >
          {copied ? '✓ 已复制' : '复制'}
        </button>
      </div>

      {/* Monaco editor */}
      <Editor
        height="200px"
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
