/**
 * BUGFIX #120 回归测试：
 *   1. 文件树深层目录可展开 —— 此前 handleExpand 加载分支只对第一层 nodes.map，
 *      第三层及更深目录（src/main）点击无反应，表现为「只能点开第二层」。
 *   2. File → Open Folder 导入工程后同步触发业务功能点 AI 提取
 *      （pickAndImportFolder 追加 importProjectAndExtract）。
 *
 * 2026-08-19 多选/右键编译：
 *   3. Ctrl+单击多选 → 工具条「编译选中」调 compileFiles（文件+目录混合）。
 *   4. 右键单项「编译此文件」同样走 compileFiles。
 *   5. 设置页「编译配置」面板读写编译配置。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const listDirEntries = vi.fn();
const compileFiles = vi.fn();
const compileConfigGet = vi.fn();
const compileConfigSave = vi.fn();
const codeNavIndex = vi.fn().mockResolvedValue({
  total_files: 1,
  total_symbols: 1,
  last_full_scan: null,
  last_incremental: null,
  is_scanning: false,
});

vi.mock('@/ipc/invoke', () => ({
  ipc: {
    listDirEntries: (...args: unknown[]) => listDirEntries(...args),
    codeNavIndex: (...args: unknown[]) => codeNavIndex(...args),
    codeNavSyncOpenedProjects: vi.fn().mockResolvedValue(undefined),
    readTextFile: vi.fn().mockResolvedValue(''),
    compileFiles: (...args: unknown[]) => compileFiles(...args),
    compileConfigGet: (...args: unknown[]) => compileConfigGet(...args),
    compileConfigSave: (...args: unknown[]) => compileConfigSave(...args),
    getWorkspace: vi.fn().mockResolvedValue({ path: 'C:/ws', custom: null, default: 'C:/ws' }),
  },
}));

vi.mock('@tauri-apps/plugin-dialog', () => ({
  open: vi.fn().mockResolvedValue('C:/code/TradeProj'),
}));

import { ProjectFileTree } from '@/components/codenav/ProjectFileTree';
import { pickAndImportFolder } from '@/components/codenav/fileOps';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useBiznavStore } from '@/store/biznavStore';

/** 虚拟目录结构：/proj → src → main → java（三层深） */
function dirResponse(path: string): Array<{ name: string; path: string; is_dir: boolean }> {
  const table: Record<string, Array<{ name: string; path: string; is_dir: boolean }>> = {
    '/proj': [
      { name: 'src', path: '/proj/src', is_dir: true },
      { name: 'pom.xml', path: '/proj/pom.xml', is_dir: false },
    ],
    '/proj/src': [{ name: 'main', path: '/proj/src/main', is_dir: true }],
    '/proj/src/main': [{ name: 'java', path: '/proj/src/main/java', is_dir: true }],
  };
  return table[path] ?? [];
}

beforeEach(() => {
  vi.clearAllMocks();
  listDirEntries.mockImplementation(async (path: string) => dirResponse(path));
  compileConfigGet.mockResolvedValue({
    javac_dir: '',
    python_dir: '',
    gcc_dir: '',
    output_dir: '',
  });
  compileFiles.mockResolvedValue({
    output_dir: 'C:/ws/compiled',
    total: 1,
    ok_count: 1,
    failed_count: 0,
    truncated: false,
    entries: [{ path: '/proj/src', ok: true, message: '.class 已输出' }],
    commands: ['javac ...'],
  });
  localStorage.clear();
  useCodeNavStore.setState({ openedProjects: [] });
});

describe('BUGFIX #120 文件树深层展开', () => {
  it('第三层目录（src/main）点击后能加载并渲染子条目', async () => {
    useCodeNavStore.setState({ openedProjects: ['/proj'] });
    render(<ProjectFileTree />);

    // mount 时自动展开项目根 → 第二层 src 出现
    await waitFor(() => expect(screen.getByText('src')).toBeTruthy());
    expect(listDirEntries).toHaveBeenCalledWith('/proj');

    // 展开第二层 src → 第三层 main 出现
    fireEvent.click(screen.getByText('src'));
    await waitFor(() => expect(screen.getByText('main')).toBeTruthy());
    expect(listDirEntries).toHaveBeenCalledWith('/proj/src');

    // 回归点：展开第三层 main → 第四层 java 必须出现（修复前此处永远加载不出来）
    fireEvent.click(screen.getByText('main'));
    await waitFor(() => expect(screen.getByText('java')).toBeTruthy());
    expect(listDirEntries).toHaveBeenCalledWith('/proj/src/main');
  });

  it('深层目录展开后可再折叠（toggle 分支同样递归生效）', async () => {
    useCodeNavStore.setState({ openedProjects: ['/proj'] });
    render(<ProjectFileTree />);

    await waitFor(() => expect(screen.getByText('src')).toBeTruthy());
    fireEvent.click(screen.getByText('src'));
    await waitFor(() => expect(screen.getByText('main')).toBeTruthy());
    fireEvent.click(screen.getByText('main'));
    await waitFor(() => expect(screen.getByText('java')).toBeTruthy());

    // 折叠第三层 → java 消失，main 行仍在
    fireEvent.click(screen.getByText('main'));
    await waitFor(() => expect(screen.queryByText('java')).toBeNull());
    expect(screen.getByText('main')).toBeTruthy();
  });
});

describe('BUGFIX #120 Open Folder 同步触发功能点提取', () => {
  it('pickAndImportFolder 导入后调用 importProjectAndExtract', async () => {
    const extractSpy = vi.fn().mockResolvedValue(undefined);
    useBiznavStore.setState({ extracting: false, importProjectAndExtract: extractSpy });

    await pickAndImportFolder();

    // 文件树来源 + codenav 索引照常
    expect(useCodeNavStore.getState().openedProjects).toContain('C:/code/TradeProj');
    expect(codeNavIndex).toHaveBeenCalledWith({ addRoots: ['C:/code/TradeProj'] });
    // 功能点提取被同步触发
    expect(extractSpy).toHaveBeenCalledWith('C:/code/TradeProj');
  });

  it('已有提取任务在途时不重复触发', async () => {
    const extractSpy = vi.fn().mockResolvedValue(undefined);
    useBiznavStore.setState({ extracting: true, importProjectAndExtract: extractSpy });

    await pickAndImportFolder();

    expect(extractSpy).not.toHaveBeenCalled();
    useBiznavStore.setState({ extracting: false });
  });
});

describe('多选 / 右键编译（2026-08-19）', () => {
  it('Ctrl+单击多选文件与目录 → 工具条「编译选中」调 compileFiles', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => undefined);
    useCodeNavStore.setState({ openedProjects: ['/proj'] });
    render(<ProjectFileTree />);
    await waitFor(() => expect(screen.getByText('src')).toBeTruthy());

    // Ctrl+单击选中目录 src + 文件 pom.xml
    fireEvent.click(screen.getByTitle('/proj/src'), { ctrlKey: true });
    fireEvent.click(screen.getByTitle('/proj/pom.xml'), { ctrlKey: true });
    expect(screen.getByText('已选 2 项')).toBeTruthy();

    fireEvent.click(screen.getByText('⚙ 编译选中'));
    await waitFor(() => expect(compileFiles).toHaveBeenCalledTimes(1));

    const [items, outputDir] = compileFiles.mock.calls[0];
    expect(items).toEqual([
      { path: '/proj/src', is_dir: true },
      { path: '/proj/pom.xml', is_dir: false },
    ]);
    // 配置无 output_dir → 回落 workspace/compiled
    expect(outputDir).toBe('C:/ws/compiled');
    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    alertSpy.mockRestore();
  });

  it('右键单项「编译此文件」只提交该条目', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => undefined);
    useCodeNavStore.setState({ openedProjects: ['/proj'] });
    render(<ProjectFileTree />);
    await waitFor(() => expect(screen.getByText('pom.xml')).toBeTruthy());

    fireEvent.contextMenu(screen.getByTitle('/proj/pom.xml'));
    fireEvent.click(screen.getByText('⚙ 编译此文件'));
    await waitFor(() => expect(compileFiles).toHaveBeenCalledTimes(1));
    expect(compileFiles.mock.calls[0][0]).toEqual([{ path: '/proj/pom.xml', is_dir: false }]);
    alertSpy.mockRestore();
  });

  it('右键目录项菜单文案为「编译此目录」', async () => {
    useCodeNavStore.setState({ openedProjects: ['/proj'] });
    render(<ProjectFileTree />);
    await waitFor(() => expect(screen.getByText('src')).toBeTruthy());

    fireEvent.contextMenu(screen.getByTitle('/proj/src'));
    expect(screen.getByText('⚙ 编译此目录')).toBeTruthy();
  });
});

describe('空态导入按钮（2026-08-28）', () => {
  it('未导入工程时展示「导入工程」按钮，点击后导入并渲染文件树', async () => {
    // 隔离功能点提取后台任务（避免真实 ipc 轮询）
    useBiznavStore.setState({
      extracting: false,
      importProjectAndExtract: vi.fn().mockResolvedValue(undefined),
    });

    render(<ProjectFileTree />);
    const btn = screen.getByText('📁 导入工程');
    expect(btn).toBeTruthy();

    fireEvent.click(btn);
    await waitFor(() =>
      expect(useCodeNavStore.getState().openedProjects).toContain('C:/code/TradeProj'),
    );
    // 导入后文件树标题栏出现，空态按钮消失
    await waitFor(() => expect(screen.getByText('打开的项目')).toBeTruthy());
    expect(screen.queryByText('📁 导入工程')).toBeNull();
  });
});
