/**
 * MonacoEditor — thin wrapper around @monaco-editor/react.
 * Configures language, theme, read-only mode.
 */
import Editor from '@monaco-editor/react';

interface Props {
  value: string;
  language: 'sql' | 'json' | 'python' | 'bash';
  readOnly?: boolean;
  onChange?: (v: string) => void;
  height?: string | number;
}

export function MonacoEditor({
  value,
  language,
  readOnly = false,
  onChange,
  height = 240,
}: Props): JSX.Element {
  return (
    <Editor
      height={height}
      language={language}
      value={value}
      theme="vs-light"
      options={{
        readOnly,
        minimap: { enabled: false },
        fontSize: 13,
        scrollBeyondLastLine: false,
        wordWrap: 'on',
      }}
      onChange={(v) => onChange?.(v ?? '')}
    />
  );
}
