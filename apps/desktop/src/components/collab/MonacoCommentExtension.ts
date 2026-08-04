/**
 * MonacoCommentExtension —— 行级评论装饰 helper（V0 函数库，非组件）。
 *
 * 用途（V0 MVP 不真接 Monaco API，但提供完整 helper 供后续 V1 接入）：
 *   - `applyLineDecorations(editor, decorations)`：在指定行号旁加 💬 图标
 *   - `buildDecoration(line, count, isActive)`：构造 IModelDeltaDecoration
 *
 * 已知约束：
 *   - `apps/desktop/src/components/editor/MonacoEditor.tsx` wrapper 没有 onMount 回调
 *   - V0 通过 `apps/desktop/src/components/audit/DiffViewer.tsx` 已经持有的 `diffEditorRef` 走旁路
 *   - V1 计划：给 MonacoEditor wrapper 加 `onMount?: (editor, monaco) => void` props
 *
 * 设计文档：[docs/design/phase-9-collab-engine.md §6.1 行级评论]
 */

import type { editor } from 'monaco-editor';

/** 单条装饰：定位 + 评论数 + 是否"未读 @ 我" */
export interface LineDecorationInput {
  line: number;
  count: number;
  /** 是否高亮（@我 / 进行中） */
  active?: boolean;
}

const SVG_GUTTER_ACTIVE =
  'data:image/svg+xml;base64,' +
  btoa(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="6" fill="#6366f1" stroke="#ffffff" stroke-width="1"/><text x="8" y="11" font-size="9" text-anchor="middle" fill="#fff" font-family="sans-serif">💬</text></svg>`,
  );
const SVG_GUTTER_INACTIVE =
  'data:image/svg+xml;base64,' +
  btoa(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="5" fill="#616161" stroke="#ffffff" stroke-width="1"/><text x="8" y="11" font-size="8" text-anchor="middle" fill="#fff" font-family="sans-serif">💬</text></svg>`,
  );

/**
 * 把 1-based 行号转 Monaco 的 IRange。
 */
function buildRange(line: number, totalLines: number): {
  startLineNumber: number;
  startColumn: number;
  endLineNumber: number;
  endColumn: number;
} {
  return {
    startLineNumber: Math.max(1, Math.min(line, totalLines)),
    startColumn: 1,
    endLineNumber: Math.max(1, Math.min(line, totalLines)),
    endColumn: 1,
  };
}

/**
 * 在 Monaco editor 上加 gutter decoration，返回旧 decoration IDs（用于清理）。
 */
export function applyLineDecorations(
  editorInstance: editor.IStandaloneCodeEditor | editor.IStandaloneDiffEditor | null | undefined,
  decorations: LineDecorationInput[],
  oldIds: string[] = [],
): string[] {
  if (!editorInstance) return oldIds;

  // 同时支持 IStandaloneCodeEditor 和 IStandaloneDiffEditor（取 modifiedEditor）
  const codeEditor: editor.IStandaloneCodeEditor | null =
    'getModifiedEditor' in editorInstance
      ? (editorInstance as editor.IStandaloneDiffEditor).getModifiedEditor()
      : (editorInstance as editor.IStandaloneCodeEditor);

  if (!codeEditor) return oldIds;

  const model = codeEditor.getModel();
  if (!model) return oldIds;
  const totalLines = model.getLineCount();

  const delta = decorations.map((d) => {
    const range = buildRange(d.line, totalLines);
    return {
      range,
      options: {
        isWholeLine: true,
        glyphMarginClassName: 'collab-gutter',
        glyphMarginHoverMessage: { value: `💬 ${d.count} 条讨论` },
        // 使用 overviewRuler / minimap 不便，这里用 linesDecorationsClassName 提示
        linesDecorationsClassName: d.active ? 'collab-line-active' : 'collab-line',
      },
    };
  });

  // V0 简化：仅使用 linesDecorationsClassName（颜色提示），不强行塞 SVG 图标到 glyphMargin
  // 真实实现时再补 glyphMargin icon
  void SVG_GUTTER_ACTIVE;
  void SVG_GUTTER_INACTIVE;

  return codeEditor.deltaDecorations(oldIds, delta);
}

/** 构造一条 input 的辅助函数 */
export function buildDecoration(input: LineDecorationInput): LineDecorationInput {
  return { line: input.line, count: input.count, active: input.active ?? false };
}

/**
 * V0 演示：在指定元素上叠加绝对定位的 💬 浮标（用于 DiffViewer 没有 onMount 的 fallback）。
 *
 * @param hostEl 容器元素（Monaco 父层）
 * @param lines  [{ line, top, count }]  —— top = lineHeight * (line - 1) + 偏移
 * @param onClick 点击回调
 */
export function overlayLineIcons(
  hostEl: HTMLElement | null,
  lines: Array<{ line: number; top: number; count: number; active?: boolean }>,
  onClick: (line: number) => void,
): () => void {
  if (!hostEl) return () => undefined;
  const style = document.createElement('style');
  style.textContent = `
    .collab-overlay-btn {
      position: absolute;
      left: 6px;
      width: 18px;
      height: 18px;
      border-radius: 9px;
      background: #6366f1;
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 10px;
      cursor: pointer;
      box-shadow: 0 0 0 1px #d0d0d0;
      z-index: 10;
      user-select: none;
    }
    .collab-overlay-btn.inactive { background: #616161; }
    .collab-overlay-btn:hover { transform: scale(1.1); }
  `;
  document.head.appendChild(style);

  const buttons: HTMLButtonElement[] = lines.map(({ line, top, count, active }) => {
    const btn = document.createElement('button');
    btn.className = `collab-overlay-btn ${active ? '' : 'inactive'}`;
    btn.style.top = `${top}px`;
    btn.title = `${count} 条讨论`;
    btn.textContent = '💬';
    btn.onclick = (e) => {
      e.stopPropagation();
      onClick(line);
    };
    hostEl.appendChild(btn);
    return btn;
  });

  return () => {
    buttons.forEach((b) => b.remove());
    style.remove();
  };
}
