/**
 * TerminalSettingPanel — 终端配置。
 *
 * shell / fontSize 通过 localStorage 持久化，key 为 `eaide.terminal.*`。
 * 实际 shell 生效待 Tauri shell/spawn 接入后在 XtermTerminal 侧读取。
 */
import { useEffect, useState } from 'react';

const LS_SHELL = 'eaide.terminal.shell';
const LS_FONT_SIZE = 'eaide.terminal.fontSize';

function readLs<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    if (v === null) return fallback;
    return JSON.parse(v) as T;
  } catch {
    return fallback;
  }
}

export function TerminalSettingPanel(): JSX.Element {
  const [fontSize, setFontSize] = useState(() => readLs<number>(LS_FONT_SIZE, 12));
  const [shell, setShell] = useState(() => readLs<string>(LS_SHELL, 'cmd.exe'));
  const [toast, setToast] = useState<string | null>(null);

  // 持久化 shell
  useEffect(() => {
    localStorage.setItem(LS_SHELL, JSON.stringify(shell));
  }, [shell]);

  // 持久化 fontSize
  useEffect(() => {
    localStorage.setItem(LS_FONT_SIZE, JSON.stringify(fontSize));
  }, [fontSize]);

  const flash = (msg: string): void => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 2000);
  };

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
              onChange={(e) => {
                const n = Number(e.target.value);
                if (!Number.isNaN(n) && n >= 8 && n <= 32) {
                  setFontSize(n);
                  flash(`字体大小已保存：${n}px`);
                }
              }}
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
            <span className="mb-1 block text-2xs text-fg-muted">
              默认 Shell（cmd.exe / pwsh / powershell / wsl）
            </span>
            <input
              value={shell}
              onChange={(e) => {
                setShell(e.target.value);
                flash(`Shell 已保存：${e.target.value}`);
              }}
              className="w-full rounded px-2 py-1 text-ui outline-none"
              style={{
                backgroundColor: '#ececec',
                color: '#1f1f1f',
                border: '1px solid #d4d4d4',
              }}
              placeholder="cmd.exe"
            />
          </label>
        </div>
      </section>
      {toast && (
        <div
          className="fixed bottom-6 left-1/2 -translate-x-1/2 rounded px-3 py-1.5 text-2xs font-medium shadow-lg"
          style={{ backgroundColor: '#1f1f1f', color: '#ffffff' }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}
