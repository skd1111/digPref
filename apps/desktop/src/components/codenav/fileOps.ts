/**
 * fileOps.ts —— Phase 2F 文件操作 helper。
 *
 * V2 用户语义（你说「如果只是打开单文件，就在对话框旁边直接打开就行了，
 *                  如果打开的是文件夹（项目），就要加载到系统资产里」）：
 *  - pickAndOpenFile()     ：Tauri 文件对话框选 1 个文件 → 在 Monaco 里直接打开
 *  - pickAndImportFolder()：Tauri 文件夹对话框选 1 个目录 → 加到系统资产 + codenav 索引
 *  - cloneFromGit()        ：V1+ 占位 —— 当前只记入索引占位
 *
 * 调用方：File 菜单 / 命令面板 / Monaco 右键
 */
import { open } from '@tauri-apps/plugin-dialog';

import { ipc } from '@/ipc/invoke';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useUIStore } from '@/store/uiStore';

interface ImportResult {
  total_files: number;
  total_symbols: number;
  last_full_scan: number | null;
  last_incremental: number | null;
  is_scanning: boolean;
}

/**
 * 在 Monaco 里打开单个文件 —— 通过 store 设置 pendingFileOpen。
 * 上层 WorkspaceLayout / CodeView 监听 store.openFile() 实现实际渲染。
 */
export async function pickAndOpenFile(): Promise<void> {
  let selected: string | string[] | null = null;
  try {
    selected = await open({
      multiple: false,
      directory: false,
      title: '选择文件',
      filters: [
        { name: '源代码', extensions: ['java', 'py', 'ts', 'tsx', 'js', 'jsx', 'go', 'rs'] },
        { name: '全部文件', extensions: ['*'] },
      ],
    });
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error('[fileOps] open file dialog failed:', e);
    window.alert(`打开文件对话框失败：${String(e)}\n请确认在 Tauri 桌面端运行（非浏览器）。`);
    return;
  }
  if (!selected) return; // 用户取消
  const filePath = Array.isArray(selected) ? selected[0] : selected;
  if (!filePath) return;

  // eslint-disable-next-line no-console
  console.info('[fileOps] opening file:', filePath);

  // 读文件内容（用 Rust 自定义 command，不需要 fs 插件权限）
  try {
    const content = await ipc.readTextFile(filePath);
    // eslint-disable-next-line no-console
    console.info('[fileOps] file read OK, length =', content.length);
    useCodeNavStore.getState().openFileInEditor({
      path: filePath,
      content,
    });
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error('[fileOps] read file failed:', e);
    useCodeNavStore.getState().recordImportError(filePath, `读取失败：${String(e)}`);
    window.alert(`读取文件失败：${String(e)}`);
    return;
  } finally {
    // 确保编辑器拆分可见（无论 readTextFile 是否成功）
    if (!useUIStore.getState().editorSplit) {
      useUIStore.getState().setEditorSplit('vertical');
    }
  }
}

/**
 * 打开文件夹 → 加到 codeNavStore.openedProjects（系统资产）+ codenav 索引。
 * V2 简化：openedProjects 列表由上层 layout 监听并渲染到 SideBar 文件树。
 */
export async function pickAndImportFolder(): Promise<void> {
  let selected: string | string[] | null = null;
  try {
    selected = await open({
      multiple: false,
      directory: true,
      title: '选择项目文件夹',
    });
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error('[fileOps] open folder dialog failed:', e);
    window.alert(`打开文件夹对话框失败：${String(e)}\n请确认在 Tauri 桌面端运行（非浏览器）。`);
    return;
  }
  if (!selected) return; // 用户取消
  const folder = Array.isArray(selected) ? selected[0] : selected;
  if (!folder) return;

  // eslint-disable-next-line no-console
  console.info('[fileOps] opening folder:', folder);

  // 1. 加到 openedProjects（侧栏文件树渲染源）
  useCodeNavStore.getState().addOpenedProject(folder);

  // 2. codenav 索引（增量）—— 后端可能未启动，失败不影响文件树展示
  try {
    const result: ImportResult = await ipc.codeNavIndex({ addRoots: [folder] });
    useCodeNavStore.getState().recordImport({
      filePath: folder,
      totalSymbols: result.total_symbols,
    });
    // eslint-disable-next-line no-console
    console.info('[fileOps] folder indexed, symbols =', result.total_symbols);
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn('[fileOps] codeNavIndex failed (backend may be offline):', e);
    useCodeNavStore.getState().recordImportError(folder, String(e));
  }
}

/** V1+ 占位：从 Git 拉仓库到本地并索引。 */
export async function cloneFromGit(): Promise<void> {
  const url = window.prompt(
    'Git 仓库 URL（例如 https://github.com/owner/repo.git）：\n' +
    '（V1 占位 — 当前不会真的拉取，请先用 git clone 到本地，再用「File → Open Folder」导入）',
  );
  if (!url) return;
  useCodeNavStore.getState().recordImportError(
    '(clone-from-git)',
    `V1 待实装：当前不会真的 clone ${url}。`,
  );
}
