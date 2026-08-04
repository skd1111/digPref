/**
 * TerminalSettingPanel — 终端配置（占位）。
 */
import { useState } from 'react';

export function TerminalSettingPanel(): JSX.Element {
  const [fontSize, setFontSize] = useState(12);
  const [shell, setShell] = useState('cmd.exe');

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-ui-lg font-semibold">Terminal · 终端</h1>
        <p className="mt-1 text-2xs text-fg-muted">
          底部 Xterm 终端的配置。生产接入 Tauri shell / spawn。
        </p>
      </header>

      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          字体
        </h2>
        <div
          className="space-y-3 rounded p-4"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          <label className="block">
            <span className="mb-1 block text-2xs text-fg-muted">Font size</span>
            <input
              type="number"
              value={fontSize}
              onChange={(e) => setFontSize(Number(e.target.value))}
              className="w-32 rounded px-2 py-1 text-ui outline-none"
              style={{
                backgroundColor: '#ececec',
                color: '#1f1f1f',
                border: '1px solid #d4d4d4',
              }}
            />
          </label>
        </div>
      </section>

      <section>
        <h2
          className="mb-2 text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          Shell
        </h2>
        <div
          className="space-y-3 rounded p-4"
          style={{ backgroundColor: '#f3f3f3', border: '1px solid #d4d4d4' }}
        >
          <label className="block">
            <span className="mb-1 block text-2xs text-fg-muted">默认 Shell</span>
            <input
              value={shell}
              onChange={(e) => setShell(e.target.value)}
              className="w-full rounded px-2 py-1 text-ui outline-none"
              style={{
                backgroundColor: '#ececec',
                color: '#1f1f1f',
                border: '1px solid #d4d4d4',
              }}
            />
          </label>
        </div>
      </section>
    </div>
  );
}
