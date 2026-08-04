/**
 * CodeNavExtension —— Phase 2F V1 收尾：Monaco 集成辅助。
 *
 * 提供两个 helper 函数（不强行注入全局）：
 *   - `registerGoToDefinition(editor, currentFile)`：注册 Monaco 右键菜单
 *     "Go to Definition"，点击后调 ipc.codeNavJump 跳转
 *   - `attachKeyboardShortcuts(editor, currentFile)`：绑定 F12 / Ctrl+K
 *     （Mac: Cmd+K）触发 Go to Definition；Escape 清除高亮
 *
 * 设计原则：
 *   - **不引入 monaco-editor 全局插件** —— 避免污染其他 Monaco 实例
 *   - **显式 attach / detach** —— 调用方控制生命周期（避免泄漏）
 *   - **失败容错** —— ipc.codeNavJump 失败时 console.warn + 兜底，不抛错阻塞 Monaco
 *
 * V1.5 补（待 Phase 4 LLM 真接入）：
 *   - "Find All References"（ipc.codeNavListSymbols 多结果 + QuickPick）
 *   - "Hover" 显示 AI 解释（ipc.codeNavExplain）
 *   - 多文件 Tab 持久化（不在 V1 范围）
 *
 * 用法（SymbolDetail.tsx 或未来多文件 Tab）：
 *   ```ts
 *   const ed = ...; // Monaco IStandaloneCodeEditor
 *   const cleanup1 = registerGoToDefinition(ed, '/path/to/file.py');
 *   const cleanup2 = attachKeyboardShortcuts(ed, '/path/to/file.py');
 *   // unmount:
 *   cleanup1(); cleanup2();
 *   ```
 */
import type { editor, IRange } from 'monaco-editor';
import { ipc } from '@/ipc/invoke';

const SHORTCUT_KEYS: { key: string; label: string }[] = [
  { key: 'F12', label: 'Go to Definition' },
  { key: 'Ctrl+K', label: 'Go to Definition' },
];

interface JumpContext {
  file: string;
  symbolName: string | null;
  range: IRange | null;
}

/** 从光标位置提取上下文（光标所在 token → jump target）。Monaco 自带 wordAtPosition。*/
function captureContext(
  editor: editor.IStandaloneCodeEditor,
  file: string,
): JumpContext {
  const position = editor.getPosition();
  if (!position) {
    return { file, symbolName: null, range: null };
  }
  const model = editor.getModel();
  if (!model) {
    return { file, symbolName: null, range: null };
  }
  const word = model.getWordAtPosition(position);
  if (!word || !word.word || /^\s*$/.test(word.word)) {
    return { file, symbolName: null, range: null };
  }
  return {
    file,
    symbolName: word.word,
    range: { startLineNumber: position.lineNumber, startColumn: word.startColumn, endLineNumber: position.lineNumber, endColumn: word.endColumn },
  };
}

/**
 * 注册 Monaco 右键菜单 "Go to Definition"。
 *
 * Returns cleanup function（必须在 unmount 调一次避免泄漏）。
 */
export function registerGoToDefinition(
  monacoEditor: editor.IStandaloneCodeEditor,
  currentFile: string,
): () => void {
  const actionId = `codenav.gotoDefinition.${currentFile}`;
  const disposable = monacoEditor.addAction({
    id: actionId,
    label: 'Go to Definition',
    contextMenuGroupId: 'codenav',
    contextMenuOrder: 1,
    keybindings: [],
    // 在 run 回调中实时捕获光标上下文（不在注册时捕获，避免捕获到空白位置）
    run: async (ed: editor.IStandaloneCodeEditor) => {
      const runCtx = captureContext(ed, currentFile);
      if (!runCtx.symbolName) {
        // eslint-disable-next-line no-console
        console.warn('[CodeNav] no symbol at cursor');
        return;
      }
      try {
        const result = await ipc.codeNavJump({
          symbol: runCtx.symbolName,
          current_file: runCtx.file,
        });
        if (result.source === 'not_found') {
          // eslint-disable-next-line no-console
          console.info('[CodeNav] not found:', runCtx.symbolName, result.note);
          return;
        }
        // 跳转：调用方需要监听 JumpResult 自己 dispatch 到对应 Monaco 实例
        // 这里用 console.info 占位（V1.5 接 store-driven navigation）
        // eslint-disable-next-line no-console
        console.info(
          '[CodeNav] jump',
          runCtx.symbolName,
          '→',
          result.file_path,
          ':',
          result.line,
        );
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn('[CodeNav] jump failed:', e);
      }
    },
  });

  return () => disposable.dispose();
}

/**
 * 绑定 F12 / Ctrl+K 触发 Go to Definition；Escape 清除高亮。
 *
 * Monaco 0.34+ 用 `addCommand` 注册 keybinding；旧 API（addAction.keybindings）已废弃。
 * 用 try/catch 保护（旧版 Monaco 退化为 silent skip）。
 */
export function attachKeyboardShortcuts(
  monacoEditor: editor.IStandaloneCodeEditor,
  currentFile: string,
): () => void {
  const disposables: Array<{ dispose: () => void }> = [];

  for (const { key } of SHORTCUT_KEYS) {
    try {
      const d = monacoEditor.addCommand(
        key === 'F12' ? 0x135 : 0x1000 + 41,
        () => {
          void triggerJump(monacoEditor, currentFile);
        },
      );
      // addCommand 返回 string | IDisposable depending on overload；unknown 中转
      const disposable = d as unknown as { dispose: () => void } | null;
      if (disposable && typeof disposable.dispose === 'function') {
        disposables.push(disposable);
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn(`[CodeNav] failed to bind shortcut ${key}:`, e);
    }
  }

  // Escape：清除 Monaco 当前模型的所有 decorations（cursor 高亮等）
  try {
    const esc = monacoEditor.addCommand(0x11 /* Escape */, () => {
      const model = monacoEditor.getModel();
      if (!model) return;
      const decos = model.getAllDecorations?.() ?? [];
      decos
        .filter((d) => d.options?.className?.includes('codenav-flash'))
        .forEach((d) => model.deltaDecorations([d.id], []));
    });
    const disposable = esc as unknown as { dispose: () => void } | null;
    if (disposable && typeof disposable.dispose === 'function') {
      disposables.push(disposable);
    }
  } catch {
    /* Escape binding best-effort */
  }

  return () => {
    for (const d of disposables) {
      try {
        d.dispose();
      } catch {
        /* best-effort */
      }
    }
  };
}

/** 触发跳转（被右键 / F12 / Ctrl+K 复用） */
async function triggerJump(
  monacoEditor: editor.IStandaloneCodeEditor,
  currentFile: string,
): Promise<void> {
  const ctx = captureContext(monacoEditor, currentFile);
  if (!ctx.symbolName) {
    return;
  }
  try {
    const result = await ipc.codeNavJump({
      symbol: ctx.symbolName,
      current_file: ctx.file,
    });
    if (result.source === 'not_found') {
      return;
    }
    // eslint-disable-next-line no-console
    console.info(
      '[CodeNav] shortcut jump',
      ctx.symbolName,
      '→',
      result.file_path,
      ':',
      result.line,
    );
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn('[CodeNav] shortcut jump failed:', e);
  }
}

/** 工具：从 Range 提取 Position（占位；V1.5 真接 store-driven navigation 用） */
export function rangeToPosition(range: IRange): { lineNumber: number; column: number } {
  return { lineNumber: range.startLineNumber, column: range.startColumn };
}