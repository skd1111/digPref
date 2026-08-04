/**
 * CodeNavExtension — Phase 2F 代码导航 Monaco 集成（V1：VSCode 风格纯索引）。
 *
 * 在 Monaco Editor 上注册：
 *   - **Ctrl+Click** 跳转定义（Monaco 原生 DefinitionProvider）—— 走 SQLite 索引
 *   - 右键菜单「📥 Open File in Editor」（Ctrl+Shift+I）
 *   - 右键菜单「🤖 AI 解释此符号」（Ctrl+K）—— **保留**，LLM 解释独立功能
 *
 * ⚠️ 与 V0 区别：
 *   - 不再有「Ctrl+F12 AI 跳转」—— 跳转一律走索引；索引未命中就提示「未找到」，
 *     避免 LLM 幻觉把用户带到错的文件。
 *   - 跳转支持原生的 Ctrl+Click + 悬停下划线（VSCode 风格）。
 *
 * ⚠️ 性能红线：跳转直接操作 Monaco DOM（revealLineInCenter + deltaDecorations），
 *    严禁把 editor instance 写进 Zustand 状态（导致渲染循环）。
 */
import { useEffect } from 'react';
import * as monaco from 'monaco-editor';

import { ipc } from '@/ipc/invoke';
import { useCodeNavStore } from '@/store/codeNavStore';

interface UseCodeNavExtensionOpts {
  editor: monaco.editor.IStandaloneCodeEditor | null;
  modelPath: string | null;
}

const FLASH_DURATION_MS = 600;
const FLASH_CLASS = 'codenav-flash-highlight';

export function useCodeNavExtension({ editor, modelPath }: UseCodeNavExtensionOpts): void {
  useEffect(() => {
    if (!editor) return;

    const cleanupProvider = registerDefinitionProvider(editor, modelPath);
    const cleanupExplain = registerExplainAction(editor);
    const cleanupImport = registerImportAction(editor, modelPath);

    return () => {
      cleanupProvider();
      cleanupExplain();
      cleanupImport();
    };
  }, [editor, modelPath]);
}

// ---------------------------------------------------------------------------
// 动作：导入当前文件到代码索引（VSCode 风格：右键菜单 + File 菜单 + 命令面板）
// ---------------------------------------------------------------------------

function registerImportAction(
  editor: monaco.editor.IStandaloneCodeEditor,
  modelPath: string | null,
): () => void {
  const action = editor.addAction({
    id: 'eaide.code-nav.import-file',
    label: '📥 Open File in Editor',
    contextMenuGroupId: 'navigation',
    contextMenuOrder: 1.4,
    keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyI],
    run: async () => {
      const filePath = modelPath ?? editor.getModel()?.uri.fsPath ?? '';
      if (!filePath) return;
      try {
        const { ipc } = await import('@/ipc/invoke');
        const content = await ipc.readTextFile(filePath);
        useCodeNavStore.getState().openFileInEditor({ path: filePath, content });
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn('[CodeNav] open file in editor failed:', e);
      }
    },
  });

  return () => action.dispose();
}

// ---------------------------------------------------------------------------
// DefinitionProvider —— Monaco 原生 Ctrl+Click 跳转（VSCode 风格）
// ---------------------------------------------------------------------------

function registerDefinitionProvider(
  editor: monaco.editor.IStandaloneCodeEditor,
  modelPath: string | null,
): () => void {
  // 用 monaco.languages.registerDefinitionProvider 而不是 addAction，
  // 这样 Monaco 自动启用：Ctrl+Click 跳转 + F12 跳转 + 悬停下划线
  const provider: monaco.IDisposable = monaco.languages.registerDefinitionProvider(
    // 全部语言都注册（用户可能打开 .java / .py / .ts / .tsx）
    // 后端 _get_query() 在 SQLite 找不到时返回空，前端拿到 not_found 就放弃
    { pattern: '**' },
    {
      async provideDefinition(model, position) {
        const word = model.getWordAtPosition(position);
        if (!word || !word.word) return null;
        const symbol = word.word;
        const currentFile = modelPath ?? model.uri.fsPath ?? '';
        try {
          const result = await ipc.codeNavJump({
            symbol,
            current_file: currentFile,
            context: model.getValue().slice(0, 4000),
            line: position.lineNumber,
          });
          // not_found：return null → Monaco 不显示下划线、不跳转
          if (result.source === 'not_found' || !result.file_path) {
            return null;
          }
          return {
            uri: monaco.Uri.file(result.file_path),
            range: {
              startLineNumber: result.line,
              endLineNumber: result.line,
              startColumn: 1,
              endColumn: 1,
            },
          };
        } catch (e) {
          // eslint-disable-next-line no-console
          console.warn('[CodeNav] definition lookup failed:', e);
          return null;
        }
      },
    },
  );

  // 同时保留一个右键菜单项（VSCode 风格「Go to Definition」），方便不用快捷键
  const action = editor.addAction({
    id: 'eaide.code-nav.goto-definition',
    label: '🔍 跳转到定义 (Go to Definition)',
    contextMenuGroupId: 'navigation',
    contextMenuOrder: 1.5,
    keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.F12],
    run: (ed: monaco.editor.IStandaloneCodeEditor) => {
      ed.trigger('keyboard', 'editor.action.revealDefinition', {});
    },
  });

  // Phase 12 V1：「📋 附加选区到对话」—— 把 Monaco 选中范围粘到 ChatInput
  // （与 AI 解释共用 readSelection helper）
  const attachAction = editor.addAction({
    id: 'eaide.code-nav.attach-selection-to-chat',
    label: '📋 附加选区到对话',
    contextMenuGroupId: 'navigation',
    contextMenuOrder: 1.55,
    run: (ed: monaco.editor.IStandaloneCodeEditor) => {
      const { selection, selectionLines } = readSelection(ed);
      if (!selection) {
        // eslint-disable-next-line no-console
        console.warn('[CodeNav] attach-selection: no real selection');
        return;
      }
      const currentFile = ed.getModel()?.uri.fsPath ?? '';
      useCodeNavStore.getState().attachChatSelection({
        file: currentFile,
        startLine: selection.startLine,
        endLine: selection.endLine,
        text: selection.text,
        label: selectionLines ?? `L${selection.startLine}-L${selection.endLine}`,
        auto: false, // 手动右键附加 → 不被自动同步覆盖
      });
      // 切到主对话面板（用 chatStore.append 或 UIStore）
      // 简化：只设置 chip，ChatInput 自动 react
    },
  });

  return () => {
    provider.dispose();
    action.dispose();
    attachAction.dispose();
  };
}

// ---------------------------------------------------------------------------
// 动作：AI 解释此符号
// ---------------------------------------------------------------------------

function registerExplainAction(
  editor: monaco.editor.IStandaloneCodeEditor,
): () => void {
  const action = editor.addAction({
    id: 'eaide.code-nav.explain',
    label: '🤖 AI 解释此符号',
    contextMenuGroupId: 'navigation',
    contextMenuOrder: 1.6,
    keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK],
    run: (ed: monaco.editor.IStandaloneCodeEditor) => {
      const { text: symbol, selection, selectionLines } = readSelection(ed);
      if (!symbol) return;
      const currentFile = ed.getModel()?.uri.fsPath ?? '';
      const line = selection?.startLine ?? ed.getSelection()?.startLineNumber ?? 0;
      useCodeNavStore.getState().requestAiExplainByContext({
        symbol,
        current_file: currentFile,
        line,
        // V1 Phase 12：选中范围传给后端 → 后端改写 prompt 为「你正在解释用户选中的代码」
        selection_start_line: selection?.startLine ?? null,
        selection_end_line: selection?.endLine ?? null,
        selection_text: selection?.text ?? null,
        selection_label: selectionLines,  // 「L120-L145 · 26 行」chip 显示用
      });
    },
  });

  // Issue 3 修复：监听选区变化 → 自动同步到 chatSelection（300ms debounce）
  // - 真选区（多行/多列）→ 自动附加 auto=true
  // - 仅光标（无选区）→ 若当前是 auto 同步来的就清掉；手动附加的保留
  let selDebounce: ReturnType<typeof setTimeout> | null = null;
  const selListener = editor.onDidChangeCursorSelection((e) => {
    if (selDebounce) clearTimeout(selDebounce);
    selDebounce = setTimeout(() => {
      const model = editor.getModel();
      if (!model) return;
      const sel = e.selection;
      const hasSelection =
        sel.startLineNumber !== sel.endLineNumber ||
        sel.startColumn !== sel.endColumn;
      const cur = useCodeNavStore.getState().chatSelection;
      if (!hasSelection) {
        // 仅清掉 auto 同步来的（手动右键附加的保留）
        if (cur?.auto) {
          useCodeNavStore.getState().clearChatSelection();
        }
        return;
      }
      const text = model.getValueInRange(sel);
      const startLine = sel.startLineNumber;
      const endLine = sel.endLineNumber;
      const lineCount = endLine - startLine + 1;
      useCodeNavStore.getState().attachChatSelection({
        file: model.uri.fsPath ?? '',
        startLine,
        endLine,
        text,
        label: `L${startLine}-L${endLine} · ${lineCount} 行`,
        auto: true, // 标记为自动同步
      });
    }, 300);
  });

  return () => {
    action.dispose();
    selListener.dispose();
    if (selDebounce) clearTimeout(selDebounce);
  };
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function readSelectionText(ed: monaco.editor.IStandaloneCodeEditor): string {
  const sel = ed.getSelection();
  if (!sel) return '';
  const model = ed.getModel();
  if (!model) return '';
  const text = model.getValueInRange(sel).trim();
  if (text) return text;
  const position = ed.getPosition();
  if (!position) return '';
  const word = model.getWordAtPosition(position);
  return word?.word ?? '';
}

/**
 * V1 Phase 12：扩展 readSelectionText，返回 symbol + 选中范围 + label。
 * 「选中」指用户多选/拖选了一段文本（不是单光标位置）。
 */
function readSelection(ed: monaco.editor.IStandaloneCodeEditor): {
  text: string;
  selection: { startLine: number; endLine: number; text: string } | null;
  selectionLines: string | null;
} {
  const sel = ed.getSelection();
  const model = ed.getModel();
  let text = '';
  if (sel && model) {
    const raw = model.getValueInRange(sel);
    text = raw.trim();
  }
  // 兜底：没拖选时取光标处单词
  if (!text) {
    text = readSelectionText(ed);
  }

  // 选中范围：必须 user 真有 selection（start != end）；多光标取第一个
  let selection: { startLine: number; endLine: number; text: string } | null = null;
  if (sel && model && (sel.startLineNumber !== sel.endLineNumber ||
                       sel.startColumn !== sel.endColumn)) {
    const raw = model.getValueInRange(sel);
    if (raw.trim()) {
      selection = {
        startLine: sel.startLineNumber,
        endLine: sel.endLineNumber,
        text: raw,
      };
    }
  }

  const selectionLines = selection
    ? `L${selection.startLine}-L${selection.endLine} · ${selection.endLine - selection.startLine + 1} 行`
    : null;

  return { text, selection, selectionLines };
}

/**
 * 跳转到指定行 + 高亮闪烁（500ms CSS animation）。
 * 直接操作 Monaco DOM，**不**触发 React 重渲染。
 */
export function revealAndFlash(ed: monaco.editor.IStandaloneCodeEditor, line: number): void {
  if (!line || line < 1) return;
  ed.revealLineInCenter(line);
  ed.setPosition({ lineNumber: line, column: 1 });
  ed.focus();
  const model = ed.getModel();
  if (!model) return;
  const id = ed.deltaDecorations(
    [],
    [{
      range: {
        startLineNumber: line,
        endLineNumber: line,
        startColumn: 1,
        endColumn: 1,
      },
      options: {
        isWholeLine: true,
        className: FLASH_CLASS,
      },
    }],
  );
  window.setTimeout(() => {
    ed.deltaDecorations(id, []);
  }, FLASH_DURATION_MS);
}
