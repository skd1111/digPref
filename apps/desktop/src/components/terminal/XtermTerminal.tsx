/**
 * XtermTerminal —— 包装 xterm.js + addon-fit，接入 Tauri shell/spawn。
 *
 * 架构：
 *   - 组件挂载 → 读 localStorage shell 设置 → 调 terminal_spawn 拉起进程
 *   - xterm onData → 普通字符走 terminal_write，特殊键走 terminal_write_key
 *   - Rust 后台读 stdout/stderr → emit Tauri event → 前端 listen → term.write()
 *   - 组件卸载 → terminal_kill 杀进程 + 清理
 *
 * 借鉴 VSCode 集成终端的设计：
 *   - 使用 ResizeObserver 自动适配容器尺寸变化
 *   - 通过 EVT 常量引用事件通道（而非硬编码字符串）
 *   - 组件卸载时正确清理终端实例、事件监听和 shell 进程
 */
import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { listen, EVT } from '@/ipc/events';
import { ipc } from '@/ipc/invoke';

const LS_FONT_SIZE = 'eaide.terminal.fontSize';
const LS_SHELL = 'eaide.terminal.shell';

function readLs<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    if (v === null) return fallback;
    return JSON.parse(v) as T;
  } catch {
    return fallback;
  }
}

function readLsFontSize(): number {
  const n = readLs<number>(LS_FONT_SIZE, 12);
  return typeof n === 'number' && n >= 8 && n <= 32 ? n : 12;
}

/** 将 xterm onData 的字符分类：特殊键 → write_key，普通字符 → write */
function classifyInput(data: string): { type: 'key'; key: string } | { type: 'text'; text: string } | null {
  // 单字符特殊键
  if (data === '\r' || data === '\n') return { type: 'key', key: 'Enter' };
  if (data === '\t') return { type: 'key', key: 'Tab' };
  if (data === '\x7f') return { type: 'key', key: 'Backspace' };
  if (data === '\x03') return { type: 'key', key: 'Ctrl+C' };
  if (data === '\x04') return { type: 'key', key: 'Ctrl+D' };

  // ANSI 转义序列（方向键等）
  if (data.startsWith('\x1b')) {
    const map: Record<string, string> = {
      '[A': 'ArrowUp',
      '[B': 'ArrowDown',
      '[C': 'ArrowRight',
      '[D': 'ArrowLeft',
      '[H': 'Home',
      '[F': 'End',
      '[3~': 'Delete',
      '[5~': 'PageUp',
      '[6~': 'PageDown',
    };
    const seq = data.slice(1); // 去掉 ESC
    if (map[seq]) return { type: 'key', key: map[seq] };
    // 未知转义序列，当普通文本发送
  }

  // 普通可打印字符
  if (data.length > 0 && data.charCodeAt(0) >= 32) {
    return { type: 'text', text: data };
  }

  return null;
}

export function XtermTerminal(): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const termIdRef = useRef<string | null>(null);
  const [status, setStatus] = useState<string>('正在启动终端…');

  useEffect(() => {
    if (!containerRef.current) return;
    let cancelled = false;

    const shell = readLs<string>(LS_SHELL, '');

    // 1. 创建 xterm 实例
    const term = new Terminal({
      fontFamily: 'JetBrains Mono, monospace',
      fontSize: readLsFontSize(),
      theme: { background: '#1e1e1e', foreground: '#d4d4d4', cursor: '#d4d4d4' },
      convertEol: true,
      cursorBlink: true,
      allowProposedApi: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();
    termRef.current = term;

    // 2. 启动 shell 进程
    (async () => {
      try {
        const [termId, exe, args] = await ipc.terminalSpawn(shell);
        if (cancelled) {
          await ipc.terminalKill(termId);
          return;
        }
        termIdRef.current = termId;
        term.writeln(`\x1b[90mShell: ${exe} ${args.join(' ')}\x1b[0m`);
        term.writeln(`\x1b[90mTerminal ID: ${termId.slice(0, 8)}…\x1b[0m`);
        term.writeln('');
        setStatus('');
      } catch (e) {
        if (!cancelled) {
          term.writeln(`\x1b[31m启动终端失败: ${String(e)}\x1b[0m`);
          setStatus(`启动失败: ${String(e)}`);
        }
      }
    })();

    // 3. xterm 输入 → Rust stdin
    const disposeOnData = term.onData((data: string) => {
      const id = termIdRef.current;
      if (!id) return;
      const classified = classifyInput(data);
      if (!classified) return;
      if (classified.type === 'key') {
        void ipc.terminalWriteKey(id, classified.key);
      } else {
        void ipc.terminalWrite(id, classified.text);
      }
    });

    // 4. 订阅 stdout 事件
    const unlistenStdout = listen<{ terminal_id: string; data: string }>(
      EVT.TERMINAL_STDOUT,
      (evt) => {
        if (evt.payload.terminal_id !== termIdRef.current) return;
        try {
          const bytes = Uint8Array.from(atob(evt.payload.data), (c) => c.charCodeAt(0));
          term.write(bytes);
        } catch {
          // base64 解码失败，忽略
        }
      },
    );

    // 5. 订阅 stderr 事件（用不同颜色显示）
    const unlistenStderr = listen<{ terminal_id: string; data: string }>(
      EVT.TERMINAL_STDERR,
      (evt) => {
        if (evt.payload.terminal_id !== termIdRef.current) return;
        try {
          const bytes = Uint8Array.from(atob(evt.payload.data), (c) => c.charCodeAt(0));
          // stderr 用黄色显示
          term.write(`\x1b[33m`);
          term.write(bytes);
          term.write(`\x1b[0m`);
        } catch {
          // ignore
        }
      },
    );

    // 6. 订阅进程退出事件
    const unlistenExit = listen<{ terminal_id: string; exit_code: number | null }>(
      EVT.TERMINAL_EXIT,
      (evt) => {
        if (evt.payload.terminal_id !== termIdRef.current) return;
        const code = evt.payload.exit_code;
        term.writeln('');
        term.writeln(
          code !== null
            ? `\x1b[90m进程已退出 (exit code: ${code})\x1b[0m`
            : '\x1b[90m进程已退出\x1b[0m',
        );
        termIdRef.current = null;
        if (!cancelled) setStatus(code !== null ? `已退出 (${code})` : '已退出');
      },
    );

    // 7. 容器尺寸变化 → 自动适配
    const resizeObserver = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        // 容器尺寸为 0 时 fit 可能失败，忽略
      }
    });
    resizeObserver.observe(containerRef.current);

    // 清理
    return () => {
      cancelled = true;
      disposeOnData.dispose();
      void unlistenStdout.then((u) => u());
      void unlistenStderr.then((u) => u());
      void unlistenExit.then((u) => u());
      resizeObserver.disconnect();

      // 杀 shell 进程
      const id = termIdRef.current;
      if (id) {
        void ipc.terminalKill(id);
        termIdRef.current = null;
      }
      term.dispose();
      termRef.current = null;
    };
  }, []);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      {status && (
        <div
          className="pointer-events-none absolute bottom-2 right-3 rounded px-2 py-0.5 text-2xs"
          style={{ backgroundColor: 'rgba(0,0,0,0.6)', color: '#aaaaaa' }}
        >
          {status}
        </div>
      )}
    </div>
  );
}
