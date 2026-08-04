/**
 * ProjectFileTree — File → Open Folder 后渲染的项目文件树。
 *
 * 功能：
 *   - 订阅 codeNavStore.openedProjects，每个项目根目录可折叠
 *   - 子目录懒加载（点击展开时调用 ipc.listDirEntries）
 *   - 单击文件 → 在 Monaco 编辑器中打开
 *   - 右键项目根 → 可移除项目
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useUIStore } from '@/store/uiStore';
import { ipc } from '@/ipc/invoke';

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
  onExpand,
  onFileClick,
}: {
  node: TreeNode;
  depth: number;
  onExpand: (node: TreeNode) => void;
  onFileClick: (node: TreeNode) => void;
}): JSX.Element {
  const paddingLeft = 12 + depth * 16;

  return (
    <div>
      <div
        className="flex cursor-pointer items-center gap-[6px] py-[3px] text-[13px] transition-colors duration-75 hover:bg-[#2a2d2e]"
        style={{ paddingLeft }}
        onClick={() => (node.isDir ? onExpand(node) : onFileClick(node))}
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
      </div>
      {/* 递归渲染子节点 */}
      {node.isDir && node.expanded && node.children && (
        <div>
          {node.children.map((child) => (
            <TreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              onExpand={onExpand}
              onFileClick={onFileClick}
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
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; folder: string } | null>(null);

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
  const handleExpand = useCallback(
    async (node: TreeNode) => {
      const nodePath = node.path;

      // 递归查找并 toggle
      const updateNode = (nodes: TreeNode[]): TreeNode[] =>
        nodes.map((n) => {
          if (n.path === nodePath) {
            return { ...n, expanded: !n.expanded };
          }
          if (n.children) {
            return { ...n, children: updateNode(n.children) };
          }
          return n;
        });

      // 如果是展开操作且没有 children，先加载
      if (!node.expanded && !node.children) {
        // 先标记 loading
        setTrees((t) => {
          const updated: Record<string, TreeNode[]> = {};
          for (const [root, nodes] of Object.entries(t)) {
            updated[root] = nodes.map((n) => {
              if (n.path === nodePath) return { ...n, loading: true };
              return n;
            });
          }
          return updated;
        });

        const children = await loadDir(nodePath);

        setTrees((t) => {
          const updated: Record<string, TreeNode[]> = {};
          for (const [root, nodes] of Object.entries(t)) {
            updated[root] = nodes.map((n) => {
              if (n.path === nodePath) return { ...n, expanded: true, loading: false, children };
              return n;
            });
          }
          return updated;
        });
      } else {
        // 折叠或已有 children 直接 toggle
        setTrees((t) => {
          const updated: Record<string, TreeNode[]> = {};
          for (const [root, nodes] of Object.entries(t)) {
            updated[root] = updateNode(nodes);
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

      {openedProjects.map((folder) => {
        const rootName = folder.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || folder;
        const isExpanded = expandedRoots.has(folder);
        const children = trees[folder];

        return (
          <div key={folder}>
            {/* 项目根行 */}
            <div
              className="flex cursor-pointer items-center gap-[6px] py-[4px] pl-2 pr-2 text-[13px] font-medium transition-colors duration-75 hover:bg-[#2a2d2e]"
              onClick={() => void toggleRoot(folder)}
              onContextMenu={(e) => {
                e.preventDefault();
                setContextMenu({ x: e.clientX, y: e.clientY, folder });
              }}
              title={folder}
            >
              <span className="w-[16px] flex-shrink-0 text-center text-[9px] text-[#616161]">
                {isExpanded ? '▾' : '▸'}
              </span>
              <span className="flex-shrink-0 text-[13px]">📂</span>
              <span className="truncate text-[#e8e8e8]">{rootName}</span>
            </div>

            {/* 子条目 */}
            {isExpanded &&
              (children ? (
                children.map((child) => (
                  <TreeItem
                    key={child.path}
                    node={child}
                    depth={1}
                    onExpand={(n) => void handleExpand(n)}
                    onFileClick={(n) => void handleFileClick(n)}
                  />
                ))
              ) : (
                <div className="py-1 pl-10 text-[12px] italic text-[#616161]">加载中…</div>
              ))}
          </div>
        );
      })}

      {/* 右键菜单：移除项目 */}
      {contextMenu && (
        <>
          <div className="fixed inset-0 z-50" onClick={() => setContextMenu(null)} />
          <div
            className="fixed z-50 min-w-[160px] rounded py-1 shadow-xl"
            style={{
              left: contextMenu.x,
              top: contextMenu.y,
              backgroundColor: '#f3f3f3',
              border: '1px solid #d0d0d0',
            }}
          >
            <button
              className="block w-full px-3 py-1 text-left text-ui text-[#333333] hover:bg-[#ececec]"
              onClick={() => {
                removeOpenedProject(contextMenu.folder);
                setExpandedRoots((prev) => {
                  const next = new Set(prev);
                  next.delete(contextMenu.folder);
                  return next;
                });
                setTrees((t) => {
                  const { [contextMenu.folder]: _, ...rest } = t;
                  return rest;
                });
                setContextMenu(null);
              }}
            >
              从工作区移除
            </button>
          </div>
        </>
      )}
    </div>
  );
}
