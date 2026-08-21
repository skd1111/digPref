/**
 * CodeEditorPane — Phase 2F 代码编辑器面板（Tab + Monaco）。
 *
 * 消费 codeNavStore 的「已打开文件」状态，把 File → Open File 选中的文件
 * 真正渲染到 Monaco 里（之前只写 store 没人渲染，导致「打开没反应」）。
 *
 * 职责：
 *   - 渲染 Tab 条（已打开文件列表，可切换 / 关闭）
 *   - 用 @monaco-editor/react 渲染激活文件（按 path 建 model，切换保留视图状态）
 *   - 接入 useCodeNavExtension（右键 AI 跳转 / 解释 / 导入）
 *   - 语法错误实时检查（2026-08-19）：内容防抖送后端 tree-sitter 校验，诊断画红色波浪线
 *   - 消费 revealTarget：跨文件跳转时自动打开目标文件 + revealLineInCenter 闪烁
 */
import { useEffect, useRef, useState } from 'react';
import Editor from '@monaco-editor/react';
import type * as monaco from 'monaco-editor';
import * as monacoEditor from 'monaco-editor';

import { ipc } from '@/ipc/invoke';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useCodeNavExtension, revealAndFlash } from './CodeNavExtension';

/** 语法检查支持的后缀（与后端 language_registry 对齐） */
const SYNTAX_CHECK_EXT = /\.(java|py|ts|tsx)$/i;
/** 内容变化 → 校验防抖（避免每敲一个字符就请求一次） */
const SYNTAX_CHECK_DEBOUNCE_MS = 500;

interface TabMenuState {
  x: number;
  y: number;
  path: string;
}

export function CodeEditorPane(): JSX.Element {
  const openFiles = useCodeNavStore((s) => s.openFiles);
  const activeFilePath = useCodeNavStore((s) => s.activeFilePath);
  const revealTarget = useCodeNavStore((s) => s.revealTarget);
  const setActiveFile = useCodeNavStore((s) => s.setActiveFile);
  const closeCodeFile = useCodeNavStore((s) => s.closeCodeFile);
  const closeOtherFiles = useCodeNavStore((s) => s.closeOtherFiles);
  const closeAllFiles = useCodeNavStore((s) => s.closeAllFiles);
  const clearRevealTarget = useCodeNavStore((s) => s.clearRevealTarget);

  const [tabMenu, setTabMenu] = useState<TabMenuState | null>(null);

  // 点击任意处 / Esc 关闭 Tab 右键菜单
  useEffect(() => {
    if (!tabMenu) return;
    const close = (): void => setTabMenu(null);
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setTabMenu(null);
    };
    window.addEventListener('click', close);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('click', close);
      window.removeEventListener('keydown', onKey);
    };
  }, [tabMenu]);

  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const [editorReady, setEditorReady] = useState(false);

  const activeFile = openFiles.find((f) => f.path === activeFilePath) ?? null;

  // 注册右键 AI 动作（跳转 / 解释 / 导入）
  useCodeNavExtension({ editor: editorRef.current, modelPath: activeFilePath });

  // 语法错误检查（2026-08-19）：编辑器内容 → 后端 tree-sitter 校验 → 红色波浪线。
  // 纯语法级（缺分号/括号不闭合）；语义错误不在范围。后端未就绪时静默不阻塞编辑。
  useEffect(() => {
    if (!editorReady || !activeFilePath) return;
    const ed = editorRef.current;
    const model = ed?.getModel() ?? null;
    if (!ed || !model) return;
    if (!SYNTAX_CHECK_EXT.test(activeFilePath)) return;

    let cancelled = false;
    let timer: number | null = null;
    const path = activeFilePath;

    const runCheck = async (): Promise<void> => {
      try {
        const res = await ipc.codeNavCheck({ file_path: path, content: model.getValue() });
        if (cancelled) return;
        // 陈旧响应防护：编辑器已切到其他 model → 丢弃不画
        if (editorRef.current?.getModel() !== model) return;
        monacoEditor.editor.setModelMarkers(
          model,
          'codenav-syntax',
          res.diagnostics.map((d) => ({
            severity: monacoEditor.MarkerSeverity.Error,
            message: d.message,
            startLineNumber: d.line,
            startColumn: d.column,
            endLineNumber: d.end_line,
            endColumn: d.end_column,
          })),
        );
      } catch {
        // Agent 未就绪 / 网络失败 → 本次不显示，下次编辑触发重新校验
      }
    };

    // 打开文件先查一次；之后内容变化防抖 500ms
    timer = window.setTimeout(() => void runCheck(), 200);
    const sub = model.onDidChangeContent(() => {
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => void runCheck(), SYNTAX_CHECK_DEBOUNCE_MS);
    });

    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
      sub.dispose();
      // 切文件/关闭时清掉旧标记，避免波浪线串到下一个文件
      monacoEditor.editor.setModelMarkers(model, 'codenav-syntax', []);
    };
  }, [editorReady, activeFilePath]);

  // revealTarget 指向未打开的文件 → 读盘后加入 Tab（跨文件跳转目标可能尚未打开）
  useEffect(() => {
    if (!revealTarget) return;
    const opened = useCodeNavStore.getState().openFiles.find((f) => f.path === revealTarget.path);
    if (opened) {
      setActiveFile(revealTarget.path);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        const content = await invoke<string>('plugin:fs|read_text_file', {
          path: revealTarget.path,
        });
        if (cancelled) return;
        useCodeNavStore.getState().openCodeFile(revealTarget.path, content);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn('[CodeEditorPane] read file for reveal failed:', e);
        clearRevealTarget();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [revealTarget, setActiveFile, clearRevealTarget]);

  // 编辑器 + 激活文件都就绪后，执行 reveal + flash
  useEffect(() => {
    if (!revealTarget || !editorReady || !editorRef.current) return;
    if (revealTarget.path !== activeFilePath) return;
    const ed = editorRef.current;
    const model = ed.getModel();
    if (!model) return;
    const id = window.setTimeout(() => {
      revealAndFlash(ed, revealTarget.line);
      clearRevealTarget();
    }, 30);
    return () => window.clearTimeout(id);
  }, [revealTarget, activeFilePath, editorReady, clearRevealTarget]);

  if (openFiles.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-fg-muted text-2xs">
        没有打开的文件
      </div>
    );
  }

  // 守卫之后保证有文件；activeFile 兜底取第一个，避免 undefined 传入 Monaco
  const file = activeFile ?? openFiles[0];

  return (
    <div
      className="flex h-full min-w-0 flex-col overflow-hidden"
      style={{ flex: 1, backgroundColor: '#ffffff' }}
    >
      {/* Tab 条 */}
      <div
        className="flex h-[30px] items-stretch overflow-x-auto border-b"
        style={{ backgroundColor: '#f3f3f3', borderColor: '#e0e0e0' }}
      >
        {openFiles.map((f) => {
          const name = f.path.split(/[\\/]/).pop() ?? f.path;
          const active = f.path === activeFilePath;
          return (
            <div
              key={f.path}
              title={f.path}
              onClick={() => setActiveFile(f.path)}
              onContextMenu={(e) => {
                e.preventDefault();
                setTabMenu({ x: e.clientX, y: e.clientY, path: f.path });
              }}
              className="group flex cursor-pointer items-center gap-1 whitespace-nowrap px-3 text-2xs"
              style={{
                backgroundColor: active ? '#ffffff' : '#ececec',
                color: active ? '#1f1f1f' : '#616161',
                borderBottom: active ? '1px solid #007acc' : '1px solid transparent',
              }}
            >
              <span>{name}</span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  closeCodeFile(f.path);
                }}
                className="ml-1 rounded px-1 opacity-0 group-hover:opacity-100 hover:bg-gray-200"
                style={{ color: '#333333' }}
                title="关闭"
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>

      {/* Monaco */}
      <div className="flex-1 overflow-hidden">
        <Editor
          height="100%"
          theme="vs-light"
          path={file.path}
          defaultLanguage={file.language}
          defaultValue={file.content}
          options={{
            minimap: { enabled: true },
            fontSize: 13,
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            automaticLayout: true,
          }}
          onMount={(ed) => {
            editorRef.current = ed;
            setEditorReady(true);
          }}
        />
      </div>

      {/* Phase 12 V1：AI 解释已迁出编辑器，渲染到右侧「控制台」。
          此处不再展示内联面板。 */}

      {tabMenu && (
        <TabContextMenu
          state={tabMenu}
          onClose={() => setTabMenu(null)}
          onCloseTab={closeCodeFile}
          onCloseOthers={closeOtherFiles}
          onCloseAll={closeAllFiles}
        />
      )}
    </div>
  );
}

// ---- Tab 右键菜单 ----

interface TabContextMenuProps {
  state: TabMenuState;
  onClose: () => void;
  onCloseTab: (path: string) => void;
  onCloseOthers: (keep: string) => void;
  onCloseAll: () => void;
}

function TabContextMenu({
  state,
  onClose,
  onCloseTab,
  onCloseOthers,
  onCloseAll,
}: TabContextMenuProps): JSX.Element {
  const fileName = state.path.split(/[\\/]/).pop() ?? state.path;

  const copy = async (text: string, label: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn(`[CodeEditorPane] copy ${label} failed:`, e);
    }
    onClose();
  };

  const reveal = async (): Promise<void> => {
    try {
      await ipc.revealInExplorer(state.path);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[CodeEditorPane] reveal in explorer failed:', e);
    }
    onClose();
  };

  const items: Array<{ label: string; onClick: () => void; danger?: boolean } | 'sep'> = [
    { label: '关闭', onClick: () => { onCloseTab(state.path); onClose(); } },
    { label: '关闭其他', onClick: () => { onCloseOthers(state.path); onClose(); } },
    { label: '关闭全部', onClick: () => { onCloseAll(); onClose(); } },
    'sep',
    { label: '复制文件名', onClick: () => void copy(fileName, 'filename') },
    { label: '复制路径', onClick: () => void copy(state.path, 'path') },
    'sep',
    { label: '在资源管理器中打开', onClick: () => void reveal() },
  ];

  return (
    <div
      className="fixed z-[200] min-w-[180px] rounded py-1 text-ui shadow-xl"
      style={{
        top: state.y,
        left: state.x,
        backgroundColor: '#f3f3f3',
        border: '1px solid #d0d0d0',
        color: '#333333',
      }}
      onClick={(e) => e.stopPropagation()}
    >
      {items.map((it, i) =>
        it === 'sep' ? (
          <div
            key={`sep-${i}`}
            style={{ height: 1, margin: '4px 0', backgroundColor: '#d0d0d0' }}
          />
        ) : (
          <button
            key={it.label}
            type="button"
            onClick={it.onClick}
            className="block w-full px-4 py-1 text-left hover:bg-gray-200"
            style={{ color: it.danger ? '#cd3131' : '#333333' }}
          >
            {it.label}
          </button>
        ),
      )}
    </div>
  );
}
