/**
 * FilePathChip —— 对话消息中的文件路径可交互化（2026-08-26）。
 *
 * 用户要求：助手输出的文件位置要能「点击直接打开」（系统默认程序），
 * 右键路径支持「在文件管理器中打开」。本模块提供：
 *   - isFilePath / renderTextWithPaths：识别 Windows 盘符 / UNC / POSIX 绝对路径，
 *     把纯文本切分成 普通文本 + FilePathChip 节点（供 Markdown 渲染器使用）；
 *   - FilePathChip：左键 = 默认程序打开；右键 = 浮层菜单
 *     （默认程序打开 / 资源管理器定位 / 复制路径；文本类追加在编辑器打开）。
 *
 * 打开失败（如文件已被清理）走轻量 alert 提示，不阻断对话流。
 */
import { useEffect, useState, type ReactNode } from 'react';
import { ipc } from '@/ipc/invoke';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useUIStore } from '@/store/uiStore';

/** 路径主体：不含空白与常见引号/括号/中文标点（防止吞掉后续文本） */
const PATH_BODY_CHAR = "[^\\s\"'`<>|*，。；！？、）】）]";
const PATH_BODY = `${PATH_BODY_CHAR}+`;

/**
 * 允许目录名含空格的路径主体（BUGFIX #170：`Enterprise AI IDE` 这类带空格目录）：
 * 内部单个空格仅当后面紧跟合法路径字符时才放行（不吃尾部空格）。
 * 只给「整体判定」（行内代码块）用——连续文本扫描仍用无空格版，
 * 跨空格吸收由 renderTextWithPaths 的扩展名锚定后处理兜底。
 */
const PATH_BODY_SPACED = `(?:${PATH_BODY_CHAR}| (?=${PATH_BODY_CHAR}))+`;

/** 三类绝对路径：盘符（C:\ / C:/）、UNC（\\server\share）、POSIX 常见根 */
function pathCore(body: string): string {
  return `(?:[A-Za-z]:[\\\\/](?:${body})|\\\\\\\\(?:${body})|/(?:home|tmp|var|opt|usr|Users)/(?:${body}))`;
}

/** 全局扫描用（识别一段文本中的所有路径；不含空格，防吞散文） */
export const FILE_PATH_RE = new RegExp(pathCore(PATH_BODY), 'g');

/** 整体判定用（行内代码是否就是一个路径；容忍内部空格） */
const FILE_PATH_EXACT_RE = new RegExp(`^${pathCore(PATH_BODY_SPACED)}$`);

/** 尾部标点修剪：句末的 。，；,.:!?)] 不属于路径 */
function trimTrailingPunct(s: string): string {
  return s.replace(/[.,;:!?)\]，。；、]+$/, '');
}

/** 判断一个字符串（去除首尾空白后）是否整体是一个文件路径 */
export function isFilePath(s: string): boolean {
  return FILE_PATH_EXACT_RE.test(trimTrailingPunct(s.trim()));
}

/** 文本类扩展名：右键菜单额外提供「在编辑器中打开」 */
const TEXT_EXTS = new Set([
  '.md', '.txt', '.py', '.ts', '.tsx', '.js', '.jsx', '.json', '.csv', '.log',
  '.yaml', '.yml', '.java', '.c', '.cpp', '.cc', '.h', '.hpp', '.sql', '.sh',
  '.bat', '.cmd', '.ps1', '.xml', '.html', '.css', '.toml', '.ini', '.conf',
]);

function isTextLike(path: string): boolean {
  const dot = path.lastIndexOf('.');
  if (dot < 0) return false;
  return TEXT_EXTS.has(path.slice(dot).toLowerCase());
}

/** 尾部扩展名锚：跨空格吸收后必须以扩展名收尾，防止吞掉后续散文（#170） */
const EXT_ANCHOR_RE = /\.[A-Za-z0-9]{1,8}$/;

/** 连续文本扫描用的无空格段匹配（跨空格吸收时逐段取用） */
const PATH_SEGMENT_RE = new RegExp(`^${PATH_BODY}`);

/**
 * 跨空格延伸（BUGFIX #170）：无空格正则命中 `C:\...\Enterprise` 后，
 * 后续「空格 + 路径字符段」逐段试吸收；只有吸收结果以扩展名收尾时
 * 才记录锚点（`Enterprise AI IDE\...\EAIDE_Intro.pptx` 能完整命中），
 * 锚点之外的散文保持原样不吞。返回延伸后的结束下标。
 */
function extendOverSpaces(text: string, start: number, end: number): number {
  let best = EXT_ANCHOR_RE.test(text.slice(start, end)) ? end : -1;
  let e = end;
  for (let hops = 0; hops < 24 && e < text.length; hops++) {
    if (text[e] !== ' ') break;
    const m = PATH_SEGMENT_RE.exec(text.slice(e + 1));
    if (!m) break;
    e += 1 + m[0].length;
    if (EXT_ANCHOR_RE.test(text.slice(start, e))) best = e;
  }
  return best > 0 ? best : end;
}

/** 把一段文本切分为 普通文本 + FilePathChip 节点（无路径时原样返回单字符串） */
export function renderTextWithPaths(text: string, keyPrefix = 'fp'): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(FILE_PATH_RE)) {
    const idx = m.index ?? 0;
    if (idx < last) continue; // 落在上一个已延伸区间内（跨空格吸收覆盖），跳过
    // 跨空格延伸：目录名含空格的路径补全（#170）
    const extendedEnd = extendOverSpaces(text, idx, idx + m[0].length);
    let path = text.slice(idx, extendedEnd);
    const trimmedPath = trimTrailingPunct(path);
    // 尾部标点还给普通文本（如「路径是 C:\a\b.pptx。」的句号）
    const tail = path.slice(trimmedPath.length);
    path = trimmedPath;
    if (idx > last) nodes.push(text.slice(last, idx));
    nodes.push(<FilePathChip key={`${keyPrefix}-${k++}`} path={path} />);
    if (tail) nodes.push(tail);
    last = extendedEnd;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** 取文件名（跨平台分隔符） */
function baseName(p: string): string {
  return p.split(/[\\/]/).filter(Boolean).pop() || p;
}

interface MenuState {
  x: number;
  y: number;
}

/** 可点击文件路径胶囊：左键默认程序打开，右键浮层菜单 */
export function FilePathChip({ path }: { path: string }): JSX.Element {
  const [menu, setMenu] = useState<MenuState | null>(null);

  // 菜单打开时，点击任意处关闭
  useEffect(() => {
    if (!menu) return;
    const close = (): void => setMenu(null);
    document.addEventListener('click', close);
    document.addEventListener('contextmenu', close);
    return () => {
      document.removeEventListener('click', close);
      document.removeEventListener('contextmenu', close);
    };
  }, [menu]);

  const openDefault = async (): Promise<void> => {
    setMenu(null);
    try {
      await ipc.openWithDefault(path);
    } catch (e) {
      window.alert(`打开文件失败：${path}\n${String(e)}`);
    }
  };

  const reveal = async (): Promise<void> => {
    setMenu(null);
    try {
      await ipc.revealInExplorer(path);
    } catch (e) {
      window.alert(`定位文件失败：${path}\n${String(e)}`);
    }
  };

  const copyPath = async (): Promise<void> => {
    setMenu(null);
    try {
      await navigator.clipboard.writeText(path);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[FilePathChip] copy path failed:', e);
    }
  };

  const openInEditor = async (): Promise<void> => {
    setMenu(null);
    try {
      const content = await ipc.readTextFile(path);
      useCodeNavStore.getState().openFileInEditor({ path, content });
      if (!useUIStore.getState().editorSplit) {
        useUIStore.getState().setEditorSplit('vertical');
      }
    } catch (e) {
      window.alert(`在编辑器中打开失败：${path}\n${String(e)}`);
    }
  };

  const items: Array<{ label: string; onClick: () => void }> = [
    { label: '用默认程序打开', onClick: () => void openDefault() },
    { label: '在资源管理器中打开', onClick: () => void reveal() },
    ...(isTextLike(path)
      ? [{ label: '在编辑器中打开', onClick: () => void openInEditor() }]
      : []),
    { label: '复制路径', onClick: () => void copyPath() },
  ];

  return (
    <>
      <button
        type="button"
        onClick={() => void openDefault()}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setMenu({ x: e.clientX, y: e.clientY });
        }}
        title={`${path}\n左键：用默认程序打开 · 右键：更多操作`}
        className="md-path-chip inline-flex max-w-full items-center gap-1 rounded border align-middle"
        style={{
          borderColor: '#bfdbfe',
          backgroundColor: '#eff6ff',
          color: '#1d4ed8',
          padding: '0 4px',
          fontSize: '0.92em',
        }}
      >
        <span aria-hidden="true" style={{ fontSize: '0.9em' }}>📄</span>
        <span className="truncate font-mono" style={{ maxWidth: 320 }}>
          {baseName(path)}
        </span>
      </button>
      {menu && (
        <div
          className="fixed z-[210] min-w-[180px] rounded py-1 text-ui shadow-xl"
          style={{
            top: menu.y,
            left: menu.x,
            backgroundColor: '#f3f3f3',
            border: '1px solid #d0d0d0',
            color: '#333333',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              onClick={it.onClick}
              className="block w-full px-4 py-1 text-left hover:bg-gray-200"
              style={{ color: '#333333' }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
