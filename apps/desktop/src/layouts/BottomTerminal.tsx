/**
 * BottomTerminal — Xterm.js powered streaming log viewer.
 * Streams:
 *   - agent reasoning chunks (<think>…</think>)
 *   - raw MCP tool-call payloads
 *   - API responses (redacted)
 *   - structured audit events
 */
import { XtermTerminal } from '@/components/terminal/XtermTerminal';

export function BottomTerminal(): JSX.Element {
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border px-3 py-1 text-xs text-fg-muted">
        LOG • agent.think · mcp.tool · mcp.result · audit.event
      </header>
      <div className="flex-1">
        <XtermTerminal />
      </div>
    </div>
  );
}