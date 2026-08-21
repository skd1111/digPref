/**
 * QueryEditor —— 数据专家中栏：SQL / Python / 对话 三合一页签。
 *
 * V1 实现：Monaco Editor（SQL/Python 语法高亮 + 自动补全 + 智能缩进）。
 * 只读铁律：SQL 含写操作时禁用「执行」并给出安全提示（前端演示；后端 guard 硬拦截）。
 * 2026-08-17 布局重构：对话从独立横向栏合并回页签（与 SQL/Python 并列），
 * 腾出的横向空间留给数据网格/图表（宽表友好）；旧持久化值 editorMode='chat'
 * 现在直接命中对话页签。
 */
import { useCallback } from 'react';
import Editor from '@monaco-editor/react';
import { useDataStore, isReadOnlySql, type EditorMode } from '@/store/dataStore';
import { DataChatPanel } from './DataChatPanel';

const MODES: Array<{ id: EditorMode; label: string; icon: string }> = [
  { id: 'sql', label: 'SQL', icon: '⌘' },
  { id: 'chat', label: '对话', icon: '💬' },
];

export function QueryEditor({ onChatZoom }: { onChatZoom?: () => void }): JSX.Element {
  const mode = useDataStore((s) => s.editorMode);
  const setMode = useDataStore((s) => s.setEditorMode);

  return (
    <div className="relative flex h-full flex-col overflow-hidden" style={{ backgroundColor: '#ffffff' }}>
      {/* 模式切换 tab */}
      <div className="flex flex-shrink-0 items-center gap-1 border-b px-2 py-1.5" style={{ borderColor: '#d0d0d0' }}>
        {MODES.map((m) => {
          const active = m.id === mode;
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => setMode(m.id)}
              className="rounded px-3 py-1 text-ui font-semibold transition-all"
              style={{
                color: active ? '#ffffff' : '#6e6e6e',
                backgroundColor: active ? '#0e639c' : 'transparent',
              }}
            >
              <span className="mr-1" aria-hidden>{m.icon}</span>
              {m.label} 模式
            </button>
          );
        })}
      </div>

      {/* 主体（SQL / 对话 页签切换；Python 模式已移除 2026-08-20） */}
      <div className="flex-1 overflow-hidden">
        {mode === 'chat' ? (
          <DataChatPanel {...(onChatZoom ? { onZoom: onChatZoom } : {})} />
        ) : (
          <SqlPane />
        )}
      </div>

      {/* HITL 重查询确认（缺口 3） */}
      <HeavyQueryConfirmDialog />
    </div>
  );
}

// ---- HITL 重查询确认对话框（后端 needs_confirm 触发） -------------------

function HeavyQueryConfirmDialog(): JSX.Element | null {
  const pending = useDataStore((s) => s.pendingConfirm);
  const confirmRun = useDataStore((s) => s.confirmRun);
  const cancelConfirm = useDataStore((s) => s.cancelConfirm);

  if (!pending) return null;
  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0, 40, 80, 0.35)' }}
      role="dialog"
      aria-label="重查询确认"
    >
      <div
        className="w-[520px] max-w-[90%] rounded-lg border shadow-xl"
        style={{ backgroundColor: '#ffffff', borderColor: '#0e639c' }}
      >
        <div
          className="flex items-center gap-2 rounded-t-lg px-4 py-2.5 text-ui font-semibold"
          style={{ backgroundColor: '#e8f1fa', color: '#0e639c' }}
        >
          🔒 重查询确认（只读）
        </div>
        <div className="space-y-3 px-4 py-3">
          <p className="text-ui" style={{ color: '#a11d1d' }}>
            ⚠ {pending.message}
          </p>
          <p className="text-2xs" style={{ color: '#616161' }}>
            该查询可能耗时较长。数据专家模式为只读（SELECT 白名单），并已强制注入 LIMIT 上限。
          </p>
          <pre
            className="max-h-[160px] overflow-auto rounded p-2 font-mono text-2xs"
            style={{ backgroundColor: '#f3f3f3', color: '#1f1f1f' }}
          >
            {pending.sql}
          </pre>
        </div>
        <div className="flex justify-end gap-2 border-t px-4 py-2.5" style={{ borderColor: '#e0e0e0' }}>
          <button
            type="button"
            onClick={cancelConfirm}
            className="rounded px-4 py-1.5 text-ui"
            style={{ backgroundColor: '#ececec', color: '#333333' }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void confirmRun()}
            className="rounded px-4 py-1.5 text-ui font-semibold"
            style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
          >
            确认执行
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- SQL 模式（Monaco）------------------------------------------------------

function SqlPane(): JSX.Element {
  const sql = useDataStore((s) => s.sqlText);
  const setSql = useDataStore((s) => s.setSql);
  const setSqlSelection = useDataStore((s) => s.setSqlSelection);
  const hasSelection = useDataStore((s) => s.sqlSelection.trim().length > 0);
  const running = useDataStore((s) => s.running);
  const runQuery = useDataStore((s) => s.runQuery);
  const selectedSourceId = useDataStore((s) => s.selectedSourceId);
  const readOnly = isReadOnlySql(sql);
  // BUGFIX #52：未选数据源时禁用执行（防止误点「执行」触发后端 400
  // 「缺少数据源连接配置」误导文案）
  const hasSource = selectedSourceId.length > 0;
  const canRun = readOnly && hasSource;

  const handleChange = useCallback(
    (value: string | undefined) => setSql(value ?? ''),
    [setSql],
  );

  const handleMount = useCallback((editor: unknown, monaco: unknown) => {
    // 注册 SQL 自动补全（表名/关键字）
    const m = monaco as {
      languages: {
        registerCompletionItemProvider: (lang: string, provider: unknown) => unknown;
      };
    };
    m.languages.registerCompletionItemProvider('sql', {
      provideCompletionItems: () => {
        const keywords = [
          'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN',
          'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT', 'OFFSET', 'AS', 'ON', 'AND', 'OR',
          'NOT', 'IN', 'BETWEEN', 'LIKE', 'IS NULL', 'IS NOT NULL', 'COUNT', 'SUM',
          'AVG', 'MAX', 'MIN', 'DISTINCT', 'UNION', 'ALL', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
        ];
        const suggestions = keywords.map((kw) => ({
          label: kw,
          kind: 17, // CompletionItemKind.Keyword
          insertText: kw,
          detail: 'SQL 关键字',
        }));
        return { suggestions };
      },
    });
    // Ctrl+Enter 执行
    const ed = editor as {
      addCommand: (keybinding: number, handler: () => void) => unknown;
      getModifiedEditor?: () => unknown;
      getModel?: () => { getValueInRange: (r: unknown) => string } | null;
      getSelection?: () => unknown;
      onDidChangeCursorSelection?: (cb: () => void) => { dispose: () => void };
    };
    ed.addCommand(2048 | 3, () => { // KeyMod.CtrlCmd | KeyCode.Enter
      if (canRun) runQuery();
    });
    // 选区同步进 store：执行时选区优先，无选区才执行全部（2026-08-20）
    const syncSelection = (): void => {
      const model = ed.getModel?.() ?? null;
      const sel = ed.getSelection?.() ?? null;
      if (!model || !sel) return;
      setSqlSelection(model.getValueInRange(sel));
    };
    ed.onDidChangeCursorSelection?.(syncSelection);
    syncSelection();
  }, [canRun, runQuery, setSqlSelection]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-hidden">
        <Editor
          language="sql"
          value={sql}
          onChange={handleChange}
          onMount={handleMount}
          theme="vs-light"
          options={{
            minimap: { enabled: false },
            fontSize: 13,
            lineNumbers: 'on',
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            tabSize: 2,
            padding: { top: 12 },
            renderLineHighlight: 'line',
            automaticLayout: true,
            suggestOnTriggerCharacters: true,
            quickSuggestions: true,
          }}
        />
      </div>
      <RunBar
        running={running}
        canRun={canRun}
        onRun={runQuery}
        note={
          !readOnly
            ? '⛔ 检测到写操作（UPDATE/DELETE/DROP…），数据专家模式只读，已禁用执行'
            : !hasSource
              ? '⚠ 未选择数据源 · 请在左侧「数据源 / 表结构」列表中点击选择一个数据源'
              : hasSelection
                ? '🎯 已选中文本 · 执行将只运行选中部分'
                : '🔒 只读查询 · Ctrl+Enter 执行 · 选中部分则只执行选区 · 多表 JOIN 需确认（HITL）'
        }
        noteColor={readOnly ? '#616161' : '#cd3131'}
      />
    </div>
  );
}

// ---- 执行栏 ----------------------------------------------------------------

function RunBar({
  running,
  canRun,
  onRun,
  note,
  noteColor,
}: {
  running: boolean;
  canRun: boolean;
  onRun: () => void;
  note: string;
  noteColor: string;
}): JSX.Element {
  return (
    <div
      className="flex flex-shrink-0 items-center justify-between border-t px-3 py-2"
      style={{ borderColor: '#d0d0d0', backgroundColor: '#f3f3f3' }}
    >
      <span className="text-2xs" style={{ color: noteColor }}>{note}</span>
      <button
        type="button"
        disabled={!canRun || running}
        onClick={onRun}
        className="rounded px-4 py-1.5 text-ui font-semibold transition-all"
        style={{
          backgroundColor: canRun ? '#059669' : '#ececec',
          color: canRun ? '#1e1e1e' : '#616161',
          cursor: canRun && !running ? 'pointer' : 'not-allowed',
          opacity: running ? 0.6 : 1,
        }}
      >
        {running ? '执行中…' : '▶ 执行'}
      </button>
    </div>
  );
}
