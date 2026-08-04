/**
 * SessionsPanel —— Phase 6 V1.5 会话管理主面板（左侧栏）。
 *
 * 功能：
 *   - 会话列表（含搜索 / 创建 / 刷新 / 删除）
 *   - 活跃会话高亮
 *   - 显示分支标记 🔀
 *   - 顶部 FTS5 搜索框（跨会话）
 *   - 底部"+ 新会话"按钮
 *
 * V1.5 (2026-07-31) 新增：搜索框、分支图标、FTS 搜索结果展示。
 */
import { useState, useEffect } from 'react';
import { useSessionsStore } from '@/store/sessionsStore';

interface Props {
  onSessionSelected?: (sessionId: string) => void;
}

export function SessionsPanel({ onSessionSelected }: Props): JSX.Element {
  const sessions = useSessionsStore((s) => s.sessions);
  const activeSessionId = useSessionsStore((s) => s.activeSessionId);
  const loading = useSessionsStore((s) => s.loading);
  const error = useSessionsStore((s) => s.error);
  const loadList = useSessionsStore((s) => s.loadList);
  const create = useSessionsStore((s) => s.create);
  const remove = useSessionsStore((s) => s.remove);
  const setActive = useSessionsStore((s) => s.setActive);
  const search = useSessionsStore((s) => s.search);
  const clearSearch = useSessionsStore((s) => s.clearSearch);
  const searchResults = useSessionsStore((s) => s.searchResults);
  const searchQuery = useSessionsStore((s) => s.searchQuery);

  const [newTitle, setNewTitle] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [searchInput, setSearchInput] = useState('');

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    const s = await create(newTitle.trim());
    if (s) {
      setNewTitle('');
      setShowCreate(false);
      onSessionSelected?.(s.id);
    }
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchInput.trim()) {
      clearSearch();
      return;
    }
    void search(searchInput.trim(), { limit: 20 });
  };

  const isSearching = searchQuery.trim().length > 0;
  type DisplayRow = {
    id: string;
    title: string;
    snippet: string;
    relevance: number;
    owner: string;
    updated_at: number;
    parent_session_id: string | null;
  };
  const displayList: DisplayRow[] = isSearching
    ? searchResults.map((h) => {
        const original = sessions.find((s) => s.id === h.session_id);
        return {
          id: h.session_id,
          title: h.title || original?.title || '(无标题)',
          snippet: h.content_snippet,
          relevance: h.relevance,
          owner: original?.owner ?? 'unknown',
          updated_at: original?.updated_at ?? 0,
          parent_session_id: original?.parent_session_id ?? null,
        };
      })
    : sessions.map((s) => ({
        id: s.id,
        title: s.title,
        snippet: '',
        relevance: 0,
        owner: s.owner,
        updated_at: s.updated_at,
        parent_session_id: s.parent_session_id,
      }));

  return (
    <div
      className="flex h-full flex-col text-sm"
      style={{ backgroundColor: '#f3f3f3', color: '#333333' }}
    >
      {/* 头部 */}
      <div
        className="flex items-center justify-between px-3 py-2"
        style={{ borderBottom: '1px solid #c0c0c0' }}
      >
        <span className="font-semibold">会话管理</span>
        <button
          type="button"
          onClick={() => void loadList()}
          disabled={loading}
          className="px-2 py-1 text-xs hover:bg-[#333]"
          title="刷新"
        >
          {loading ? '⏳' : '🔄'}
        </button>
      </div>

      {/* 搜索框 */}
      <form onSubmit={handleSearch} className="px-3 py-2" style={{ borderBottom: '1px solid #c0c0c0' }}>
        <input
          type="search"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="🔍 FTS5 搜索标题 / 消息 / 工具"
          className="w-full rounded px-2 py-1 text-xs"
          style={{
            backgroundColor: '#ececec',
            color: '#333333',
            border: '1px solid #c0c0c0',
          }}
        />
      </form>

      {/* 错误提示 */}
      {error && (
        <div
          className="px-3 py-2 text-xs"
          style={{ backgroundColor: '#fdeaea', color: '#cd3131' }}
        >
          ⚠️ {error}
        </div>
      )}

      {/* 列表 */}
      <div className="flex-1 overflow-y-auto">
        {displayList.length === 0 && (
          <div className="px-3 py-4 text-xs text-center" style={{ color: '#616161' }}>
            {isSearching ? '无搜索结果' : '暂无会话'}
          </div>
        )}
        {displayList.map((s) => (
          <div
            key={s.id}
            onClick={() => {
              setActive(s.id);
              onSessionSelected?.(s.id);
              if (isSearching) clearSearch();
            }}
            className="cursor-pointer border-l-2 px-3 py-2 hover:bg-[#2a2d2e]"
            style={{
              backgroundColor: activeSessionId === s.id ? '#0e639c' : 'transparent',
              borderLeftColor: activeSessionId === s.id ? '#007acc' : 'transparent',
              borderBottom: '1px solid #c0c0c0',
            }}
          >
            <div className="flex items-center justify-between">
              <span className="truncate font-medium">{s.title}</span>
              {s.parent_session_id && <span title="分支会话">🔀</span>}
            </div>
            <div className="mt-1 flex items-center gap-2 text-xs" style={{ color: '#616161' }}>
              <span>{s.owner}</span>
              <span>·</span>
              <span>{new Date(s.updated_at).toLocaleDateString()}</span>
            </div>
            {isSearching && s.snippet && (
              <div
                className="mt-1 truncate text-xs"
                style={{ color: '#0b6bcb' }}
                dangerouslySetInnerHTML={{ __html: s.snippet }}
              />
            )}
            {!isSearching && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm(`确认删除会话「${s.title}」？`)) {
                    void remove(s.id);
                  }
                }}
                className="mt-1 text-xs hover:underline"
                style={{ color: '#cd3131' }}
              >
                删除
              </button>
            )}
          </div>
        ))}
      </div>

      {/* 创建会话 */}
      <div className="px-3 py-2" style={{ borderTop: '1px solid #c0c0c0' }}>
        {showCreate ? (
          <div className="flex flex-col gap-1">
            <input
              type="text"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="新会话标题"
              className="w-full rounded px-2 py-1 text-xs"
              style={{
                backgroundColor: '#ececec',
                color: '#333333',
                border: '1px solid #c0c0c0',
              }}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              autoFocus
            />
            <div className="flex gap-1">
              <button
                type="button"
                onClick={handleCreate}
                className="flex-1 rounded px-2 py-1 text-xs"
                style={{ backgroundColor: '#0e639c', color: '#fff' }}
              >
                创建
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowCreate(false);
                  setNewTitle('');
                }}
                className="rounded px-2 py-1 text-xs hover:bg-[#333]"
              >
                取消
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            className="w-full rounded px-2 py-1 text-xs hover:bg-[#333]"
          >
            + 新会话
          </button>
        )}
      </div>
    </div>
  );
}