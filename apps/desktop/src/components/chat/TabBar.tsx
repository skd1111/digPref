/**
 * TabBar — VSCode 风格的多 tab 标签栏。
 *
 * 显示当前所有打开的会话；单击切换；右侧 "+" 新建；右上角关闭按钮。
 * 当前 active tab 顶部有蓝色 2px 边框，背景与编辑器同色。
 */
import { useEffect, useState } from 'react';
import { ipc } from '@/ipc/invoke';
import { useChatStore, type ChatTab } from '@/store/chatStore';
import { useUIStore } from '@/store/uiStore';

/** AI 标题摘要每个 tab 只尝试一次（2026-08-07，失败不重试避免刷屏） */
const aiTitledTabs = new Set<string>();

export function TabBar(): JSX.Element {
  const allTabs = useChatStore((s) => s.tabs);
  // 模式隔离（2026-08-11）：只展示当前模式的页签，专家团对话不串进开发模式
  const mode = useUIStore((s) => s.mode);
  const targetMode = mode === 'operator' ? 'operator' : 'full';
  const tabs = allTabs.filter((t) => (t.mode ?? 'full') === targetMode);
  const activeId = useChatStore((s) => s.activeTabId);
  const newTab = useChatStore((s) => s.newTab);
  const closeTab = useChatStore((s) => s.closeTab);
  const switchTab = useChatStore((s) => s.switchTab);
  const renameTab = useChatStore((s) => s.renameTab);
  const moveTab = useChatStore((s) => s.moveTab);

  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const busy = useChatStore((s) => s.busy);

  // AI 标题（2026-08-07）：首轮对话完成后（user + assistant 都有且流已停），
  // 用 AI 摘要替换自动截断标题；手动改过的标题不动；失败保留截断标题
  useEffect(() => {
    if (busy) return;
    for (const tab of allTabs) {
      if (aiTitledTabs.has(tab.id)) continue;
      const firstUser = tab.messages.find((m) => m.role === 'user' && m.content);
      const firstAssistant = tab.messages.find((m) => m.role === 'assistant' && m.content);
      if (!firstUser || !firstAssistant) continue;
      const provisional = (firstUser.content ?? '').slice(0, 30).replace(/\n/g, ' ');
      if (tab.title !== '新会话' && tab.title !== provisional) continue;
      aiTitledTabs.add(tab.id);
      void ipc
        .summarizeTitle(firstUser.content ?? '', firstAssistant.content ?? '')
        .then((r) => {
          const t = (r.title ?? '').trim();
          if (t) renameTab(tab.id, t);
        })
        .catch(() => undefined);
    }
  }, [allTabs, busy, renameTab]);

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
      style={{ backgroundColor: '#f5f5f4', borderBottom: '1px solid #e7e5e4' }}
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
              backgroundColor: isActive ? '#ffffff' : '#f5f5f4',
              color: isActive ? '#202124' : '#6b7280',
              borderColor: '#e7e5e4',
              borderTop: isActive ? '1px solid #10a37f' : '1px solid transparent',
              borderLeft: dragOverId === tab.id ? '2px solid #10a37f' : '2px solid transparent',
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
                style={{ color: '#202124' }}
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
