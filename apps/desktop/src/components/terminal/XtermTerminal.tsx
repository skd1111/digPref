/**
 * XtermTerminal —— 包装 xterm.js + addon-fit，订阅 Tauri 事件流。
 *
 * 借鉴 VSCode 集成终端的设计：
 *   - 使用 ResizeObserver 自动适配容器尺寸变化
 *   - 通过 EVT 常量引用事件通道（而非硬编码字符串）
 *   - 组件卸载时正确清理终端实例和事件监听
 */
import { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { listen, EVT } from '@/ipc/events';
import { isMockText } from '@/lib/mockFilter';

export function XtermTerminal(): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      fontFamily: 'JetBrains Mono, monospace',
      fontSize: 12,
      theme: { background: '#ffffff', foreground: '#1f1f1f' },
      convertEol: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();
    termRef.current = term;

    // 监听容器尺寸变化，自动调整终端大小（VSCode 集成终端行为）
    const resizeObserver = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        // 容器尺寸为 0 时 fit 可能失败，忽略
      }
    });
    resizeObserver.observe(containerRef.current);

    // 订阅日志事件（使用 EVT 常量，与 Rust 侧保持同步）
    const unlistenP = listen<string>(EVT.AGENT_LOG, (line) => {
      // 终端同样不显示任何 mock 数据
      if (!isMockText(line.payload)) {
        term.writeln(line.payload);
      }
    });

    return () => {
      void unlistenP.then((u) => u());
      resizeObserver.disconnect();
      term.dispose();
    };
  }, []);

  return <div ref={containerRef} className="h-full w-full" />;
}
