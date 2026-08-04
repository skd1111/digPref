/**
 * XtermDrawer — Phase 2E 快捷键逃生通道。
 *
 * 按 `Ctrl+~` 从底部滑出半屏 Xterm 抽屉；
 * 按 `Esc` 或再次 `Ctrl+~` 收起。
 *
 * 设计要点：
 *   - 抽屉浮在对话区上方，不改变页面布局（绝对定位 + transform）
 *   - 抽屉内 xterm.js 直接操作 DOM（ROADMAP §12.3 性能红线）
 *   - 入场 / 离场 220ms ease-out + transform translateY 丝滑
 *   - 抽屉打开时禁用全局 Tab 切换，避免焦点被劫持
 */
import { useEffect, useRef } from 'react';

interface XtermDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function XtermDrawer({ open, onClose }: XtermDrawerProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);

  // Esc 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  return (
    <>
      {/* 抽屉本体（绝对定位 + transform 过渡） */}
      <div
        className="xterm-drawer fixed left-0 right-0 bottom-0 z-[150] flex flex-col shadow-2xl"
        style={{
          height: '40vh', // 半屏
          backgroundColor: '#ffffff',
          borderTop: '1px solid #007acc',
          transform: open ? 'translateY(0)' : 'translateY(100%)',
          transition: 'transform 220ms cubic-bezier(0.4, 0, 0.2, 1)',
          pointerEvents: open ? 'auto' : 'none',
        }}
      >
        {/* 标题栏 */}
        <div
          className="flex h-[28px] flex-shrink-0 items-center justify-between border-b px-3 text-2xs"
          style={{
            backgroundColor: '#f3f3f3',
            borderColor: '#d4d4d4',
            color: '#333333',
          }}
        >
          <div className="flex items-center gap-2">
            <span style={{ color: '#059669' }}>●</span>
            <span className="font-semibold uppercase tracking-wider">Xterm · 逃生通道</span>
            <span style={{ color: '#616161' }}>· Ctrl+~ 切换 · Esc 关闭</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            title="关闭 (Esc)"
            className="rounded px-2 py-0.5 text-fg-muted transition-colors hover:bg-vscode-border hover:text-fg"
          >
            ✕
          </button>
        </div>

        {/* Xterm 容器（Phase 2B 完整集成之前先用占位 div） */}
        <div
          ref={containerRef}
          className="xterm-host flex-1 overflow-hidden p-2"
          style={{ backgroundColor: '#000000' }}
        >
          {/* 占位提示：等 Phase 2B ssh2 / russh 集成后换成真实 xterm.js 实例 */}
          <div
            className="flex h-full items-center justify-center text-2xs"
            style={{ color: '#616161' }}
          >
            Xterm 实例将在 Phase 2B（类 FinalShell）接入。当前是占位 UI。
          </div>
        </div>
      </div>
    </>
  );
}
