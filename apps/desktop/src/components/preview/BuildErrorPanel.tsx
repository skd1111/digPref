/**
 * BuildErrorPanel —— Vite 编译错误覆盖层（红底 + 一键复制日志）。
 */
import { useState } from "react";

export interface BuildErrorPanelProps {
  error: string;
  file?: string | null;
  line?: number | null;
  column?: number | null;
}

export function BuildErrorPanel({
  error,
  file,
  line,
  column,
}: BuildErrorPanelProps) {
  const [copied, setCopied] = useState(false);
  const location = [
    file,
    line != null ? `:${line}` : "",
    column != null ? `:${column}` : "",
  ]
    .join("")
    .trim();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(error);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div
      className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-red-950/85 p-6 text-red-100"
      data-testid="build-error-panel"
    >
      <div className="text-sm font-semibold uppercase tracking-wider text-red-300">
        ✕ Build error
      </div>
      {location && (
        <div
          className="max-w-full truncate font-mono text-xs text-red-300"
          title={location}
        >
          {location}
        </div>
      )}
      <pre className="max-h-48 max-w-full overflow-auto whitespace-pre-wrap rounded bg-black/40 p-3 font-mono text-xs leading-relaxed">
        {error}
      </pre>
      <button
        type="button"
        onClick={copy}
        className="rounded bg-red-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-600"
      >
        {copied ? "✓ 已复制" : "复制错误日志"}
      </button>
    </div>
  );
}
