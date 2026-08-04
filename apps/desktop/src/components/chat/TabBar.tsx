/**
 * TabBar — VSCode 风格的多 tab 标签栏。
 *
 * 显示当前所有打开的会话；单击切换；右侧 "+" 新建；右上角关闭按钮。
 * 当前 active tab 顶部有蓝色 2px 边框，背景与编辑器同色。
 */
import { useState } from 'react';
import { useChatStore, type ChatTab } from '@/store/chatStore';

export function TabBar(): JSX.Element {
  const tabs = useChatStore((s) => s.tabs);
  const activeId = useChatStore((s) => s.activeTabId);
  const newTab = useChatStore((s) => s.newTab);
  const closeTab = useChatStore((s) => s.closeTab);
  const switchTab = useChatStore((s) => s.switchTab);
  const renameTab = useChatStore((s) => s.renameTab);
  const moveTab = useChatStore((s) => s.moveTab);

  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [dragOverId, setDragOverId] = useState<string | null>(null);

  // 当用户发首条消息时，自动从首条 user 消息更新 tab 标题
  const autoTitleIfEmpty = (tab: ChatTab): void => {
    if (tab.title !== '新会话') return;
    const first = tab.messages.find((m) => m.role === 'user');
    if (first) {
      const t = (first.content ?? '').slice(0, 30).replace(/\n/g, ' ');
      renameTab(tab.id, t || '新会话');
      // 同时把首条 user 消息作为这个 tab 的"线索"
    }
  };

  return (
    <div
      className="flex h-[35px] select-none items-stretch overflow-x-auto"
      style={{ backgroundColor: '#f3f3f3', borderBottom: '1px solid #e0e0e0' }}
    >
      {tabs.map((tab) => {
        // 第一次见到时尝试自动标题
        if (tab.messages.length > 0 && tab.title === '新会话') {
          queueMicrotask(() => autoTitleIfEmpty(tab));
        }
        const isActive = tab.id === activeId;
        return (
          <div
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData('text/plain', tab.id);
              e.dataTransfer.effectAllowed = 'move';
            }}
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = 'move';
              if (dragOverId !== tab.id) setDragOverId(tab.id);
            }}
            onDragLeave={() => {
              if (dragOverId === tab.id) setDragOverId(null);
            }}
            onDrop={(e) => {
              e.preventDefault();
              const src = e.dataTransfer.getData('text/plain');
              if (src && src !== tab.id) moveTab(src, tab.id);
              setDragOverId(null);
            }}
            onClick={() => switchTab(tab.id)}
            onDoubleClick={() => {
              setEditing(tab.id);
              setEditValue(tab.title);
            }}
            className="group relative flex h-full cursor-pointer items-center gap-2 border-r px-3 text-ui"
            style={{
              minWidth: 120,
              maxWidth: 240,
              backgroundColor: isActive ? '#ffffff' : '#ececec',
              color: isActive ? '#1f1f1f' : '#6e6e6e',
              borderColor: '#e0e0e0',
              borderTop: isActive ? '1px solid #007acc' : '1px solid transparent',
              borderLeft: dragOverId === tab.id ? '2px solid #007acc' : '2px solid transparent',
            }}
          >
            {/* 关闭按钮 */}
            {tabs.length > 1 && (
              <button
                type="button"
                title="关闭"
                onClick={(e) => {
                  e.stopPropagation();
                  closeTab(tab.id);
                }}
                className="rounded px-1 text-fg-muted opacity-0 transition-opacity hover:bg-vscode-border hover:text-fg group-hover:opacity-100"
              >
                ✕
              </button>
            )}

            {/* 标题 / 编辑 */}
            {editing === tab.id ? (
              <input
                autoFocus
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onBlur={() => {
                  renameTab(tab.id, editValue.trim() || '新会话');
                  setEditing(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    renameTab(tab.id, editValue.trim() || '新会话');
                    setEditing(null);
                  } else if (e.key === 'Escape') {
                    setEditing(null);
                  }
                }}
                className="flex-1 bg-transparent text-ui outline-none"
                style={{ color: '#1f1f1f' }}
              />
            ) : (
              <span className="flex-1 truncate">
                {tab.title}
                {tab.messages.length > 0 && (
                  <span className="ml-1.5 text-2xs opacity-50">
                    ({tab.messages.length})
                  </span>
                )}
              </span>
            )}
          </div>
        );
      })}

      {/* 新建按钮 */}
      <button
        type="button"
        title="新建会话"
        onClick={() => newTab()}
        className="flex h-full w-[35px] items-center justify-center text-fg-muted transition-colors hover:bg-vscode-border hover:text-fg"
      >
        ＋
      </button>
    </div>
  );
}
