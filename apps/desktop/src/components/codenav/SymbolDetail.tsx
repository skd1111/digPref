/**
 * SymbolDetail —— 选中符号的 Monaco 代码片段展示 + AI 跳转到定义。
 *
 * 行为（V0）：
 *   1. 渲染当前 symbol 的真实 snippet（从 store 拿）
 *   2. 挂载完成后（onMount）revealLineInCenter(startLine) + setPosition
 *   3. deltaDecorations 加 codenav-flash 黄色高亮 500ms
 *   4. 切换 symbol 时清理旧高亮 + 重新跳行
 *   5. 底部 AiExplainPanel 接收 store.aiExplanation 渲染
 *
 * 关键约束（设计文档 §9 性能红线）：
 *   跳转直接操作 Monaco DOM，**禁进 Zustand 状态管理**。
 *
 * V1 TODO：接 Tauri invoke('code_nav_jump') 真实后端 + LLM 推断。
 */
import { useEffect, useRef, useState } from 'react';
import Editor, { type OnMount } from '@monaco-editor/react';
import type { editor } from 'monaco-editor';
import { selectSelectedSymbol, useCodeNavStore } from '@/store/codeNavStore';
import { KIND_COLORS, LANGUAGE_COLORS } from '@/types/codenav';
import { AiExplainPanel } from './AiExplainPanel';

const FLASH_DURATION_MS = 500;

export function SymbolDetail(): JSX.Element {
  // ===== 所有 hooks 必须在任何早期 return 之前调用（React Rules of Hooks） =====
  const symbol = useCodeNavStore(selectSelectedSymbol);
  const requestAiExplain = useCodeNavStore((s) => s.requestAiExplain);
  const clearAiExplanation = useCodeNavStore((s) => s.clearAiExplanation);
  const [aiAutoRequested, setAiAutoRequested] = useState(false);

  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const flashIdsRef = useRef<string[]>([]);

  // 切换 symbol 时：清理旧高亮 + 跳到新行
  // 注意：editorRef.current 可能为 null（symbol 已切换但 Editor 还在 mount 中），此时跳过
  useEffect(() => {
    if (!symbol) return;
    if (!editorRef.current) return;

    const ed = editorRef.current;

    // 1) 清掉旧高亮
    if (flashIdsRef.current.length > 0) {
      ed.deltaDecorations(flashIdsRef.current, []);
      flashIdsRef.current = [];
    }

    // 2) revealLineInCenter + setPosition + focus
    ed.revealLineInCenter(symbol.start_line);
    ed.setPosition({ lineNumber: symbol.start_line, column: 1 });
    ed.focus();

    // 3) 加新高亮
    const ids = ed.deltaDecorations([], [
      {
        range: {
          startLineNumber: symbol.start_line,
          endLineNumber: symbol.end_line,
          startColumn: 1,
          endColumn: 1,
        },
        options: {
          isWholeLine: true,
          className: 'codenav-flash',
          linesDecorationsClassName: 'codenav-flash-gutter',
        },
      },
    ]);
    flashIdsRef.current = ids;

    // 4) 500ms 后清掉
    const timer = setTimeout(() => {
      if (editorRef.current && flashIdsRef.current.length > 0) {
        editorRef.current.deltaDecorations(flashIdsRef.current, []);
        flashIdsRef.current = [];
      }
    }, FLASH_DURATION_MS);

    return () => {
      clearTimeout(timer);
      // 卸载时清掉高亮
      if (editorRef.current && flashIdsRef.current.length > 0) {
        editorRef.current.deltaDecorations(flashIdsRef.current, []);
        flashIdsRef.current = [];
      }
    };
  }, [symbol?.id, symbol?.start_line, symbol?.end_line]);

  // 切换 symbol 时清掉旧 AI 解释；首次选中自动请求一次
  useEffect(() => {
    if (!symbol) {
      clearAiExplanation();
      setAiAutoRequested(false);
      return;
    }
    clearAiExplanation();
    setAiAutoRequested(false);
  }, [symbol?.id, clearAiExplanation]);

  useEffect(() => {
    if (symbol && !aiAutoRequested) {
      void requestAiExplain(symbol.id);
      setAiAutoRequested(true);
    }
  }, [symbol?.id, aiAutoRequested, requestAiExplain]);

  // 卸载时清理 editor ref（@monaco-editor/react 自行管理 dispose 生命周期）
  useEffect(() => {
    return () => {
      editorRef.current = null;
    };
  }, []);

  // ===== 早期 return 必须在所有 hooks 之后 =====
  if (!symbol) {
    return (
      <div
        className="flex h-full items-center justify-center p-6 text-center text-2xs"
        style={{ color: '#616161' }}
      >
        ← 从左侧选择符号查看详情
      </div>
    );
  }

  const kind = KIND_COLORS[symbol.kind];
  const lang = LANGUAGE_COLORS[symbol.language];

  const handleMount: OnMount = (ed) => {
    editorRef.current = ed;
  };

  return (
    <div className="flex h-full flex-col">
      {/* 标题区 */}
      <div
        className="flex-shrink-0 border-b p-3"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
      >
        <div className="mb-1.5 flex items-center gap-2">
          <span
            className="rounded px-1.5 py-0.5 text-2xs font-semibold"
            style={{ backgroundColor: kind.bg, color: kind.fg }}
          >
            {kind.label}
          </span>
          <span
            className="rounded px-1.5 py-0.5 text-2xs font-semibold"
            style={{ backgroundColor: lang.bg, color: lang.fg }}
          >
            {lang.label}
          </span>
          {symbol.parent_class && (
            <span className="text-2xs" style={{ color: '#616161' }}>
              · {symbol.parent_class}
            </span>
          )}
          <span className="ml-auto text-2xs" style={{ color: '#616161' }}>
            L{symbol.start_line}–L{symbol.end_line}
          </span>
        </div>
        <h2 className="font-mono text-base font-semibold" style={{ color: '#1f1f1f' }}>
          {symbol.name}
        </h2>
        {symbol.signature && (
          <div
            className="mt-1 truncate font-mono text-2xs"
            style={{ color: '#0b6bcb' }}
            title={symbol.signature}
          >
            {symbol.signature}
          </div>
        )}
        <div className="mt-1 text-2xs" style={{ color: '#616161' }} title={symbol.file_path}>
          📄 {symbol.file_path}
        </div>
      </div>

      {/* Monaco 代码 snippet */}
      <div className="flex-1 overflow-hidden">
        <Editor
          value={symbol.snippet}
          language={symbol.language}
          theme="vs-light"
          onMount={handleMount}
          options={{
            readOnly: true,
            minimap: { enabled: false },
            fontSize: 12,
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            lineNumbers: 'on',
            renderLineHighlight: 'all',
            renderWhitespace: 'none',
            folding: true,
            fontFamily: '"Cascadia Code", "JetBrains Mono", Consolas, monospace',
            padding: { top: 8, bottom: 8 },
          }}
        />
      </div>

      {/* AI 解释面板 */}
      <AiExplainPanel
        symbolId={symbol.id}
        onRequestExplain={() => void requestAiExplain(symbol.id)}
      />

      {/* 高亮闪烁 CSS（注入一次即可，多次挂载会重复但无害） */}
      <style>{CODENAV_FLASH_CSS}</style>
    </div>
  );
}

const CODENAV_FLASH_CSS = `
  .codenav-flash {
    background-color: rgba(255, 213, 79, 0.25) !important;
    transition: background-color 200ms ease-out;
  }
  .codenav-flash-gutter {
    background-color: #ffd54f !important;
    width: 3px !important;
    margin-left: 2px;
  }
`;
