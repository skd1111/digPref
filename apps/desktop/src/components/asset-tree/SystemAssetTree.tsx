/**
 * SystemAssetTree — VSCode 风格的可分组资源树。
 *
 * 功能：
 *   - 按 type 分组（Database / REST / SSH / RPA），每组可折叠
 *   - 节点单击 → 选中（高亮）→ 双击 → "pin 到 chat"（未来实装）
 *   - 右键 → 上下文菜单（编辑 / 复制 / 复制名称 / 删除）
 */
import { useMemo, useState, useCallback } from 'react';
import { useAssetStore, type AssetNode } from '@/store/assetStore';
import { AssetConfigDialog } from './AssetConfigDialog';

const TYPE_ICON: Record<AssetNode['type'], string> = {
  database: '🗄',
  rest: '🌐',
  ssh: '🔐',
  rpa: '🤖',
};

const TYPE_LABEL: Record<AssetNode['type'], string> = {
  database: 'DATABASE',
  rest: 'REST API',
  ssh: 'SSH',
  rpa: 'RPA',
};

export function SystemAssetTree(): JSX.Element {
  const tree = useAssetStore((s) => s.tree);
  const refresh = useAssetStore((s) => s.refresh);
  const removeAsset = useAssetStore((s) => s.removeAsset);
  const addAsset = useAssetStore((s) => s.addAsset);
  const updateAsset = useAssetStore((s) => s.updateAsset);

  // 只展示真实资产（不注入任何 demo / mock 数据）
  const nodes: AssetNode[] = tree;

  // 按 type 分组
  const grouped = useMemo(() => {
    const g: Record<string, AssetNode[]> = {};
    for (const n of nodes) {
      (g[n.type] ||= []).push(n);
    }
    return g;
  }, [nodes]);

  // 默认全部展开
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; node: AssetNode } | null>(null);
  // 空态右键菜单：树里没有节点时，右键空白区域也可「新增资产」
  const [emptyMenu, setEmptyMenu] = useState<{ x: number; y: number } | null>(null);
  const [editingNode, setEditingNode] = useState<AssetNode | null>(null);
  const [editLabel, setEditLabel] = useState('');
  const [configNode, setConfigNode] = useState<AssetNode | null>(null);
  const [showAddDialog, setShowAddDialog] = useState(false);

  // ---- 右键菜单动作 ----
  const handleCopyName = useCallback(async (node: AssetNode) => {
    try {
      await navigator.clipboard.writeText(node.label);
    } catch { /* ignore */ }
    setContextMenu(null);
  }, []);

  const handleDuplicate = useCallback((node: AssetNode) => {
    const copy: AssetNode = {
      ...node,
      id: `${node.id}_copy_${Date.now()}`,
      label: `${node.label} (副本)`,
      meta: { ...node.meta },
    };
    addAsset(copy);
    setContextMenu(null);
  }, [addAsset]);

  const handleDelete = useCallback((node: AssetNode) => {
    removeAsset(node.id);
    setContextMenu(null);
  }, [removeAsset]);

  const handleEdit = useCallback((node: AssetNode) => {
    setEditingNode(node);
    setEditLabel(node.label);
    setContextMenu(null);
  }, []);

  const commitEdit = useCallback(() => {
    if (editingNode && editLabel.trim()) {
      updateAsset(editingNode.id, { label: editLabel.trim() });
    }
    setEditingNode(null);
  }, [editingNode, editLabel, updateAsset]);

  return (
    <div
      className="px-2 py-1 text-ui"
      onClick={() => { setContextMenu(null); setEmptyMenu(null); }}
      onContextMenu={(e) => {
        // 节点自身的右键菜单优先；空白区域（未配置资产时）提供「新增资产」入口
        if (tree.length === 0 && !(e.target as HTMLElement).closest('li')) {
          e.preventDefault();
          setEmptyMenu({ x: e.clientX, y: e.clientY });
        }
      }}
    >
      <div className="mb-2 flex items-center justify-between px-1">
        <span className="text-2xs text-fg-muted">系统资产 ({nodes.length})</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setShowAddDialog(true)}
            className="rounded px-1 text-fg-muted hover:bg-vscode-border hover:text-fg"
            title="新增资产"
          >
            ＋
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            className="rounded px-1 text-fg-muted hover:bg-vscode-border hover:text-fg"
            title="刷新"
          >
            ↻
          </button>
        </div>
      </div>

      {(Object.keys(grouped) as AssetNode['type'][]).map((type) => (
        <section key={type} className="mb-1">
          <button
            type="button"
            onClick={() => {
              const next = new Set(collapsed);
              if (next.has(type)) next.delete(type);
              else next.add(type);
              setCollapsed(next);
            }}
            className="flex w-full items-center gap-1 px-1 py-0.5 text-2xs font-semibold uppercase tracking-wider hover:text-fg"
            style={{ color: '#333333' }}
          >
            <span className="text-fg-muted">{collapsed.has(type) ? '▸' : '▾'}</span>
            <span>{TYPE_LABEL[type]}</span>
            <span className="text-fg-muted">({grouped[type].length})</span>
          </button>
          {!collapsed.has(type) && (
            <ul>
              {grouped[type].map((n) => (
                <li
                  key={n.id}
                  onClick={() => setSelected(n.id)}
                  onDoubleClick={() => alert(`Pin "${n.label}" to chat — 实装中`)}
                  onContextMenu={(e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    setContextMenu({ x: e.clientX, y: e.clientY, node: n });
                  }}
                  className="group flex cursor-pointer items-center gap-2 rounded px-1 py-0.5"
                  style={{
                    backgroundColor: selected === n.id ? '#ececec' : 'transparent',
                    color: '#333333',
                  }}
                >
                  <span className="text-fg-muted">{TYPE_ICON[n.type]}</span>
                  <span className="truncate">{n.label}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}

      {tree.length === 0 && (
        <div className="mt-2 px-2 text-2xs text-fg-muted">
          未配置系统资产 — 点击右上 ＋ 新增，或编辑
          %APPDATA%/eaide/systems.yaml 后点击 ↻ 刷新。右键空白处也可新增。
        </div>
      )}

      {emptyMenu && (
        <div
          className="fixed z-[100] min-w-[160px] rounded py-1 shadow-xl"
          style={{
            top: emptyMenu.y,
            left: emptyMenu.x,
            backgroundColor: '#f3f3f3',
            border: '1px solid #d0d0d0',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            onClick={() => { setShowAddDialog(true); setEmptyMenu(null); }}
            className="block w-full px-3 py-1 text-left text-ui hover:bg-vscode-border"
            style={{ color: '#333333' }}
          >
            ＋ 新增资产
          </button>
          <button
            type="button"
            onClick={() => { void refresh(); setEmptyMenu(null); }}
            className="block w-full px-3 py-1 text-left text-ui hover:bg-vscode-border"
            style={{ color: '#333333' }}
          >
            ↻ 刷新
          </button>
        </div>
      )}

      {contextMenu && (
        <div
          className="fixed z-[100] min-w-[160px] rounded py-1 shadow-xl"
          style={{
            top: contextMenu.y,
            left: contextMenu.x,
            backgroundColor: '#f3f3f3',
            border: '1px solid #d0d0d0',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {[
            { label: '配置', action: () => { setConfigNode(contextMenu.node); setContextMenu(null); } },
            { label: 'Edit', action: () => handleEdit(contextMenu.node) },
            { label: 'Duplicate', action: () => handleDuplicate(contextMenu.node) },
            { label: 'Copy Name', action: () => void handleCopyName(contextMenu.node) },
            { label: '—', action: () => {} },
            { label: 'Delete', action: () => handleDelete(contextMenu.node), danger: true },
          ].map((it, i) =>
            it.label === '—' ? (
              <div key={i} className="my-0.5 h-px" style={{ backgroundColor: '#d0d0d0' }} />
            ) : (
              <button
                key={it.label}
                type="button"
                onClick={it.action}
                className="block w-full px-3 py-1 text-left text-ui hover:bg-vscode-border"
                style={{ color: it.danger ? '#cd3131' : '#333333' }}
              >
                {it.label}
              </button>
            ),
          )}
        </div>
      )}

      {/* 资产配置弹窗 */}
      {(configNode || showAddDialog) && (
        <AssetConfigDialog
          node={configNode}
          onClose={() => { setConfigNode(null); setShowAddDialog(false); }}
        />
      )}

      {/* 编辑弹窗 */}
      {editingNode && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50"
          onClick={() => setEditingNode(null)}
        >
          <div
            className="w-[320px] rounded-lg p-4 shadow-2xl"
            style={{ backgroundColor: '#f3f3f3', border: '1px solid #d0d0d0' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 text-sm font-medium" style={{ color: '#333333' }}>
              编辑资产名称
            </div>
            <input
              autoFocus
              value={editLabel}
              onChange={(e) => setEditLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitEdit();
                if (e.key === 'Escape') setEditingNode(null);
              }}
              className="w-full rounded px-2 py-1.5 text-sm outline-none"
              style={{ backgroundColor: '#ececec', color: '#333333', border: '1px solid #c0c0c0' }}
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setEditingNode(null)}
                className="rounded px-3 py-1 text-xs hover:bg-gray-200"
                style={{ color: '#333333' }}
              >
                取消
              </button>
              <button
                type="button"
                onClick={commitEdit}
                className="rounded px-3 py-1 text-xs text-white"
                style={{ backgroundColor: '#007acc' }}
              >
                确定
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
