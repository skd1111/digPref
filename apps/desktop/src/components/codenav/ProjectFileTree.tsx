/**
 * ProjectFileTree — File → Open Folder 后渲染的项目文件树。
 *
 * 功能：
 *   - 订阅 codeNavStore.openedProjects，每个项目根目录可折叠
 *   - 子目录懒加载（点击展开时调用 ipc.listDirEntries）
 *   - 单击文件 → 在 Monaco 编辑器中打开
 *   - Ctrl/⌘+单击 多选文件/目录；右键或工具条触发编译（2026-08-19）
 *   - 右键项目根 → 可移除项目
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useUIStore } from '@/store/uiStore';
import { useOfficePreviewStore } from '@/store/officePreviewStore';
import { ipc } from '@/ipc/invoke';

/** Office 文档后缀（V9 右键预览入口判定） */
const OFFICE_SUFFIX_RE = /\.(docx|xlsx|pptx)$/i;

interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  children?: TreeNode[];
  expanded?: boolean;
  loading?: boolean;
}

/** 单个文件/目录行 */
function TreeItem({
  node,
  depth,
  isPathSelected,
  onExpand,
  onFileClick,
  onToggleSelect,
  onContextMenuNode,
}: {
  node: TreeNode;
  depth: number;
  isPathSelected: (path: string) => boolean;
  onExpand: (node: TreeNode) => void;
  onFileClick: (node: TreeNode) => void;
  onToggleSelect: (node: TreeNode) => void;
  onContextMenuNode: (e: React.MouseEvent, node: TreeNode) => void;
}): JSX.Element {
  const paddingLeft = 12 + depth * 16;
  const isSelected = isPathSelected(node.path);

  return (
    <div>
      <div
        className="flex cursor-pointer items-center gap-[6px] py-[3px] text-[13px] transition-colors duration-75 hover:bg-[#e8e8e8]"
        style={{ paddingLeft, backgroundColor: isSelected ? '#d6e4f0' : undefined }}
        onClick={(e) => {
          // Ctrl/⌘+单击 → 多选（编译用）；普通单击维持展开/打开行为
          if (e.ctrlKey || e.metaKey) {
            onToggleSelect(node);
            return;
          }
          if (node.isDir) onExpand(node);
          else onFileClick(node);
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onContextMenuNode(e, node);
        }}
        title={node.path}
      >
        {/* 展开/折叠箭头 */}
        {node.isDir ? (
          <span className="w-[16px] flex-shrink-0 text-center text-[9px] text-[#616161]">
            {node.loading ? '⋯' : node.expanded ? '▾' : '▸'}
          </span>
        ) : (
          <span className="w-[16px] flex-shrink-0" />
        )}
        {/* 图标 */}
        <span className="flex-shrink-0 text-[13px] opacity-80">{node.isDir ? '📁' : '📄'}</span>
        {/* 名称 */}
        <span className="truncate text-[#333333]">{node.name}</span>
        {/* 选中勾（多选编译，2026-08-19） */}
        {isSelected && (
          <span className="ml-auto pr-2 text-[11px] text-[#0451a5]">✓</span>
        )}
      </div>
      {/* 递归渲染子节点 */}
      {node.isDir && node.expanded && node.children && (
        <div>
          {node.children.map((child) => (
            <TreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              isPathSelected={isPathSelected}
              onExpand={onExpand}
              onFileClick={onFileClick}
              onToggleSelect={onToggleSelect}
              onContextMenuNode={onContextMenuNode}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function ProjectFileTree(): JSX.Element | null {
  const openedProjects = useCodeNavStore((s) => s.openedProjects);
  const removeOpenedProject = useCodeNavStore((s) => s.removeOpenedProject);

  // 每个项目根目录对应一棵树
  const [trees, setTrees] = useState<Record<string, TreeNode[]>>({});
  const [expandedRoots, setExpandedRoots] = useState<Set<string>>(new Set());
  // 右键菜单目标（2026-08-19 泛化：文件/目录/项目根都可作为编译对象）
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    path: string;
    isDir: boolean;
    isRoot: boolean;
  } | null>(null);
  // 多选编译（2026-08-19）：path → isDir；Ctrl/⌘+单击切换
  const [selected, setSelected] = useState<Map<string, boolean>>(new Map());
  const [compileBusy, setCompileBusy] = useState(false);

  // 持久化展开的目录 + 已加载的子树（重启 EAIDE 后保留）
  // 用 localStorage 即可；只存 path 集合，不存完整文件列表（文件内容易过期）。
  useEffect(() => {
    try {
      const raw = localStorage.getItem('eaide.codenav.expandedRoots');
      if (raw) setExpandedRoots(new Set(JSON.parse(raw) as string[]));
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(
        'eaide.codenav.expandedRoots',
        JSON.stringify([...expandedRoots]),
      );
    } catch {
      /* ignore */
    }
  }, [expandedRoots]);

  const loadDir = useCallback(async (dirPath: string): Promise<TreeNode[]> => {
    try {
      const entries = await ipc.listDirEntries(dirPath);
      return entries.map((e) => ({
        name: e.name,
        path: e.path,
        isDir: e.is_dir,
      }));
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[ProjectFileTree] loadDir failed:', dirPath, e);
      return [];
    }
  }, []);

  // Issue 2 修复：mount 时 + openedProjects 变化时为「已展开但未加载」的项目加载子树
  // （修复 File → Open Folder 后永远卡在「加载中…」的 bug）
  const treesRef = useRef(trees);
  treesRef.current = trees;
  const loadDirRef = useRef(loadDir);
  loadDirRef.current = loadDir;
  const prevProjectsRef = useRef<string[]>([]);
  useEffect(() => {
    const prev = new Set(prevProjectsRef.current);
    const isNewlyAdded = (folder: string): boolean => !prev.has(folder);

    for (const folder of openedProjects) {
      const alreadyLoaded = treesRef.current[folder] !== undefined;
      const shouldExpand =
        expandedRoots.has(folder) ||
        (prevProjectsRef.current.length === 0 && isNewlyAdded(folder));

      if (shouldExpand && !alreadyLoaded) {
        // 第一次打开（localStorage 没存过）→ 自动展开
        if (!expandedRoots.has(folder) && isNewlyAdded(folder)) {
          setExpandedRoots((prevSet) => {
            if (prevSet.has(folder)) return prevSet;
            return new Set(prevSet).add(folder);
          });
        }
        void loadDirRef.current(folder).then((children) => {
          setTrees((t) => ({ ...t, [folder]: children }));
        });
      }
    }
    prevProjectsRef.current = openedProjects;
  }, [openedProjects, expandedRoots]);

  // 展开/折叠项目根目录
  const toggleRoot = useCallback(
    async (folder: string) => {
      setExpandedRoots((prev) => {
        const next = new Set(prev);
        if (next.has(folder)) {
          next.delete(folder);
        } else {
          next.add(folder);
          // 首次展开时加载子条目
          if (!trees[folder]) {
            void loadDir(folder).then((children) => {
              setTrees((t) => ({ ...t, [folder]: children }));
            });
          }
        }
        return next;
      });
    },
    [trees, loadDir],
  );

  // 展开子目录（递归更新 trees state）
  // BUGFIX #120：加载分支必须递归查找目标节点 —— 此前只对第一层 nodes.map，
  // 第三层及更深目录（如 src/main）的 loading/children 永远写不进去，表现为「只能点开第二层」。
  const handleExpand = useCallback(
    async (node: TreeNode) => {
      const nodePath = node.path;

      // 递归查找目标节点并应用 patch（深层路径需穿透 children）
      const patchDeep = (
        nodes: TreeNode[],
        patch: (n: TreeNode) => TreeNode,
      ): TreeNode[] =>
        nodes.map((n) => {
          if (n.path === nodePath) {
            return patch(n);
          }
          if (n.children) {
            return { ...n, children: patchDeep(n.children, patch) };
          }
          return n;
        });

      // 如果是展开操作且没有 children，先加载
      if (!node.expanded && !node.children) {
        // 先标记 loading
        setTrees((t) => {
          const updated: Record<string, TreeNode[]> = {};
          for (const [root, nodes] of Object.entries(t)) {
            updated[root] = patchDeep(nodes, (n) => ({ ...n, loading: true }));
          }
          return updated;
        });

        const children = await loadDir(nodePath);

        setTrees((t) => {
          const updated: Record<string, TreeNode[]> = {};
          for (const [root, nodes] of Object.entries(t)) {
            updated[root] = patchDeep(nodes, (n) => ({
              ...n,
              expanded: true,
              loading: false,
              children,
            }));
          }
          return updated;
        });
      } else {
        // 折叠或已有 children 直接 toggle
        setTrees((t) => {
          const updated: Record<string, TreeNode[]> = {};
          for (const [root, nodes] of Object.entries(t)) {
            updated[root] = patchDeep(nodes, (n) => ({ ...n, expanded: !n.expanded }));
          }
          return updated;
        });
      }
    },
    [loadDir],
  );

  // 单击文件 → 在编辑器中打开
  const handleFileClick = useCallback(async (node: TreeNode) => {
    try {
      const content = await ipc.readTextFile(node.path);
      useCodeNavStore.getState().openFileInEditor({ path: node.path, content });
      if (!useUIStore.getState().editorSplit) {
        useUIStore.getState().setEditorSplit('vertical');
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[ProjectFileTree] open file failed:', node.path, e);
    }
  }, []);

  // ---- 多选 + 编译（2026-08-19）------------------------------------------

  const isPathSelected = useCallback((path: string): boolean => selected.has(path), [selected]);

  const toggleSelect = useCallback((path: string, isDir: boolean) => {
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(path)) next.delete(path);
      else next.set(path, isDir);
      return next;
    });
  }, []);

  /** 解析输出目录：编译配置 output_dir → workspace/compiled → 空串（Rust 兜底） */
  const resolveOutputDir = useCallback(async (): Promise<string> => {
    try {
      const cfg = await ipc.compileConfigGet();
      if (cfg.output_dir && cfg.output_dir.trim()) return cfg.output_dir.trim();
    } catch {
      /* 配置读取失败 → 试 workspace */
    }
    try {
      const ws = await ipc.getWorkspace();
      if (ws?.path) return `${ws.path.replace(/[\\/]+$/, '')}/compiled`;
    } catch {
      /* Agent 离线 → 空串让 Rust 兜底到安装目录/workspace/compiled */
    }
    return '';
  }, []);

  /** 编译指定条目（文件或目录），完成后弹窗汇总 */
  const runCompile = useCallback(
    async (items: Array<{ path: string; isDir: boolean }>) => {
      if (items.length === 0 || compileBusy) return;
      setCompileBusy(true);
      try {
        const outputDir = await resolveOutputDir();
        const report = await ipc.compileFiles(
          items.map((it) => ({ path: it.path, is_dir: it.isDir })),
          outputDir,
        );
        const lines: string[] = [
          `编译完成：成功 ${report.ok_count} / 共 ${report.total}`,
          `输出目录：${report.output_dir}`,
        ];
        if (report.truncated) lines.push('（源文件超过单次上限 2000，已截断）');
        const failures = report.entries.filter((e) => !e.ok && e.path);
        if (failures.length > 0) {
          lines.push(`失败 ${report.failed_count} 个文件：`);
          for (const f of failures.slice(0, 3)) {
            lines.push(`  ${f.path}\n    ${f.message.slice(0, 400)}`);
          }
          if (failures.length > 3) lines.push(`  … 其余 ${failures.length - 3} 个略`);
        }
        if (report.commands.length > 0) {
          lines.push('', '执行的命令：');
          for (const c of report.commands) lines.push(`  ${c}`);
        }
        window.alert(lines.join('\n'));
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn('[ProjectFileTree] compile failed:', e);
        window.alert(`编译失败：${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setCompileBusy(false);
      }
    },
    [compileBusy, resolveOutputDir],
  );

  const compileSelected = useCallback(() => {
    const items = [...selected.entries()].map(([path, isDir]) => ({ path, isDir }));
    void runCompile(items);
  }, [selected, runCompile]);

  if (!openedProjects || openedProjects.length === 0) return null;

  return (
    <div className="py-1" onContextMenu={(e) => e.preventDefault()}>
      {/* 标题栏：VSCode section header 风格 */}
      <div
        className="flex items-center justify-between px-3 py-[5px]"
      >
        <span className="text-[11px] font-semibold uppercase tracking-wide text-[#bbbbbb]">
          打开的项目
        </span>
        <span className="text-[10px] text-[#616161]">{openedProjects.length}</span>
      </div>

      {/* 多选编译操作提示（2026-08-19） */}
      <div className="px-3 pb-1 text-[10px] text-[#8a8a8a]">
        Ctrl+单击多选，右键或工具条编译
      </div>

      {/* 多选编译工具条（2026-08-19）：Ctrl/⌘+单击选中后浮现 */}
      {selected.size > 0 && (
        <div
          className="mx-2 mb-1 flex items-center gap-2 rounded px-2 py-1"
          style={{ backgroundColor: '#e8f0fe', border: '1px solid #c5d9f0' }}
        >
          <span className="text-[11px] text-[#0451a5]">已选 {selected.size} 项</span>
          <button
            type="button"
            disabled={compileBusy}
            onClick={compileSelected}
            className="rounded px-2 py-0.5 text-[11px] font-semibold text-white disabled:opacity-50"
            style={{ backgroundColor: '#007acc' }}
          >
            {compileBusy ? '编译中…' : '⚙ 编译选中'}
          </button>
          <button
            type="button"
            onClick={() => setSelected(new Map())}
            className="ml-auto rounded px-1.5 py-0.5 text-[11px] text-[#616161] hover:bg-[#d0e0f5]"
          >
            清除
          </button>
        </div>
      )}

      {openedProjects.map((folder) => {
        const rootName = folder.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || folder;
        const isExpanded = expandedRoots.has(folder);
        const children = trees[folder];

        return (
          <div key={folder}>
            {/* 项目根行（Ctrl/⌘+单击可加入编译多选；浅色主题：悬停浅灰、选中浅蓝） */}
            <div
              className="flex cursor-pointer items-center gap-[6px] py-[4px] pl-2 pr-2 text-[13px] font-medium transition-colors duration-75 hover:bg-[#e8e8e8]"
              style={{ backgroundColor: selected.has(folder) ? '#d6e4f0' : undefined }}
              onClick={(e) => {
                if (e.ctrlKey || e.metaKey) {
                  toggleSelect(folder, true);
                  return;
                }
                void toggleRoot(folder);
              }}
              onContextMenu={(e) => {
                e.preventDefault();
                setContextMenu({ x: e.clientX, y: e.clientY, path: folder, isDir: true, isRoot: true });
              }}
              title={folder}
            >
              <span className="w-[16px] flex-shrink-0 text-center text-[9px] text-[#616161]">
                {isExpanded ? '▾' : '▸'}
              </span>
              <span className="flex-shrink-0 text-[13px]">📂</span>
              <span className="truncate text-[#333333]">{rootName}</span>
              {selected.has(folder) && (
                <span className="ml-auto text-[11px] text-[#0451a5]">✓</span>
              )}
            </div>

            {/* 子条目 */}
            {isExpanded &&
              (children ? (
                children.map((child) => (
                  <TreeItem
                    key={child.path}
                    node={child}
                    depth={1}
                    isPathSelected={isPathSelected}
                    onExpand={(n) => void handleExpand(n)}
                    onFileClick={(n) => void handleFileClick(n)}
                    onToggleSelect={(n) => toggleSelect(n.path, n.isDir)}
                    onContextMenuNode={(e, n) =>
                      setContextMenu({ x: e.clientX, y: e.clientY, path: n.path, isDir: n.isDir, isRoot: false })
                    }
                  />
                ))
              ) : (
                <div className="py-1 pl-10 text-[12px] italic text-[#616161]">加载中…</div>
              ))}
          </div>
        );
      })}

      {/* 右键菜单：编译 + 移除项目（2026-08-19 泛化） */}
      {contextMenu && (
        <>
          <div className="fixed inset-0 z-50" onClick={() => setContextMenu(null)} />
          <div
            className="fixed z-50 min-w-[180px] rounded py-1 shadow-xl"
            style={{
              left: contextMenu.x,
              top: contextMenu.y,
              backgroundColor: '#f3f3f3',
              border: '1px solid #d0d0d0',
            }}
          >
            <button
              className="block w-full px-3 py-1 text-left text-ui text-[#333333] hover:bg-[#ececec] disabled:opacity-50"
              disabled={compileBusy}
              onClick={() => {
                const target = { path: contextMenu.path, isDir: contextMenu.isDir };
                setContextMenu(null);
                void runCompile([target]);
              }}
            >
              {compileBusy ? '编译中…' : `⚙ 编译此${contextMenu.isDir ? '目录' : '文件'}`}
            </button>
            {selected.size > 0 && (
              <button
                className="block w-full px-3 py-1 text-left text-ui text-[#333333] hover:bg-[#ececec] disabled:opacity-50"
                disabled={compileBusy}
                onClick={() => {
                  setContextMenu(null);
                  compileSelected();
                }}
              >
                ⚙ 编译已选 {selected.size} 项
              </button>
            )}
            {/* V9 Office 预览（2026-08-25）：仅 docx/xlsx/pptx 文件显示 */}
            {!contextMenu.isDir && OFFICE_SUFFIX_RE.test(contextMenu.path) && (
              <button
                className="block w-full px-3 py-1 text-left text-ui text-[#333333] hover:bg-[#ececec]"
                onClick={() => {
                  const target = contextMenu.path;
                  setContextMenu(null);
                  void useOfficePreviewStore.getState().openPreview(target);
                }}
              >
                📄 Office 预览
              </button>
            )}
            {/* V9.6 HTML 演示预览（2026-08-25）：视觉演示稿（单文件 HTML）直读展示 */}
            {!contextMenu.isDir && /\.html$/i.test(contextMenu.path) && (
              <button
                className="block w-full px-3 py-1 text-left text-ui text-[#333333] hover:bg-[#ececec]"
                onClick={() => {
                  const target = contextMenu.path;
                  setContextMenu(null);
                  void useOfficePreviewStore.getState().openHtml(target);
                }}
              >
                🎨 HTML 演示预览
              </button>
            )}
            {contextMenu.isRoot && (
              <button
                className="block w-full px-3 py-1 text-left text-ui text-[#333333] hover:bg-[#ececec]"
                onClick={() => {
                  removeOpenedProject(contextMenu.path);
                  setExpandedRoots((prev) => {
                    const next = new Set(prev);
                    next.delete(contextMenu.path);
                    return next;
                  });
                  setTrees((t) => {
                    const { [contextMenu.path]: _, ...rest } = t;
                    return rest;
                  });
                  setContextMenu(null);
                }}
              >
                从工作区移除
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
