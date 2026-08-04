/**
 * useShortcuts — 全局键盘快捷键（VSCode 风格）。
 *
 * 已知支持的快捷键：
 *   - Ctrl+Shift+P → 命令面板
 *   - Ctrl+P       → 快速打开
 *   - F1           → CheatSheet
 *   - Esc          → 关闭所有弹窗
 *   - Ctrl+T       → 新建 tab
 *   - Ctrl+W       → 关闭当前 tab
 *   - Ctrl+B       → 切换侧边栏
 *   - Ctrl+J       → 切换底部终端
 *   - F12          → 切换开发者工具（config.yaml devtools 开关；编辑器内为跳转定义）
 *   - Ctrl+Shift+I → 切换开发者工具（编辑器内为「导入文件到编辑器」）
 *   - Ctrl+,       → 打开 Settings
 *   - Ctrl+`       → 新建终端（占位）
 */
import { useEffect } from 'react';
import { ipc } from '@/ipc/invoke';
import { useUIStore } from '@/store/uiStore';
import { useChatStore } from '@/store/chatStore';

export function useShortcuts(): void {
  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      const target = e.target as HTMLElement;
      const isInEditor =
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable;

      const cmd = e.ctrlKey || e.metaKey;
      const shift = e.shiftKey;

      // 代码编辑器（Monaco / CodeMirror）内部：F12 是「跳转定义」、
      // Ctrl+Shift+I 是「导入文件到编辑器」，不拦截为开发者工具。
      const isInCodeEditor =
        typeof target.closest === 'function' &&
        !!target.closest('.monaco-editor, .cm-editor, .CodeMirror');

      // F12 / Ctrl+Shift+I — 切换开发者工具（受 config.yaml 的 devtools 开关控制）
      if (!isInCodeEditor) {
        const isF12 = e.key === 'F12' && !cmd && !shift && !e.altKey;
        const isDevToolsChord =
          cmd && shift && (e.key === 'I' || e.key === 'i') && !e.altKey;
        if (isF12 || isDevToolsChord) {
          e.preventDefault();
          void ipc
            .openDevtools()
            .then((msg) => {
              // eslint-disable-next-line no-console
              console.info(`[devtools] ${msg}`);
            })
            .catch((err) => {
              // eslint-disable-next-line no-console
              console.warn('[devtools]', err);
            });
          return;
        }
      }

      // F1 — CheatSheet（任何时候都响应）
      if (e.key === 'F1' && !cmd && !shift) {
        e.preventDefault();
        useUIStore.getState().toggleCheatSheet();
        return;
      }

      // Esc — 关闭所有弹窗
      if (e.key === 'Escape') {
        const ui = useUIStore.getState();
        if (ui.commandPaletteOpen) {
          ui.toggleCommandPalette(false);
          e.preventDefault();
          return;
        }
        if (ui.quickOpenOpen) {
          ui.toggleQuickOpen(false);
          e.preventDefault();
          return;
        }
        if (ui.cheatSheetOpen) {
          ui.toggleCheatSheet(false);
          e.preventDefault();
          return;
        }
      }

      // 下面这些快捷键不要在输入框里拦截
      if (isInEditor && !(cmd || e.key === 'F1')) return;

      // Ctrl+Shift+P — Command Palette
      if (cmd && shift && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault();
        useUIStore.getState().toggleCommandPalette();
        return;
      }

      // Ctrl+P — Quick Open
      if (cmd && !shift && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault();
        useUIStore.getState().toggleQuickOpen();
        return;
      }

      // Ctrl+T — 新建 tab
      if (cmd && (e.key === 'T' || e.key === 't')) {
        e.preventDefault();
        useChatStore.getState().newTab();
        return;
      }

      // Ctrl+W — 关闭当前 tab
      if (cmd && (e.key === 'W' || e.key === 'w')) {
        e.preventDefault();
        const cs = useChatStore.getState();
        cs.closeTab(cs.activeTabId);
        return;
      }

      // Ctrl+, — Settings
      if (cmd && e.key === ',') {
        e.preventDefault();
        window.history.pushState({}, '', '/settings');
        window.dispatchEvent(new PopStateEvent('popstate'));
        return;
      }
    };

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);
}
