/**
 * CollabCenter —— 协作中心主入口（Phase 9 V0 MVP）。
 *
 * 布局（占满 SideBar 区域 260px + 推到 Center）：
 *   ┌─────────────┬─────────────────────────────────────┐
 *   │             │  选中 context：TaskDiscussionPanel   │
 *   │ ContextList │  (含参与者 + CommentThread + 编辑器) │
 *   │  (单列)     │                                      │
 *   │             │                                      │
 *   │  ↕ Tab       │                                      │
 *   │  ↕ 搜索     │                                      │
 *   └─────────────┴─────────────────────────────────────┘
 *
 * 入口：ActivityBar 选中 `collab` 图标 → SideBar 切换为 CollabCenter。
 */
import { useCollabStore } from '@/store/collabStore';
import { ContextList } from '@/components/collab/ContextList';
import { TaskDiscussionPanel } from '@/components/collab/TaskDiscussionPanel';
import { COLLAB_ACCENT, type CollabCenterTab } from '@/types/collab';

const TABS: Array<{ key: CollabCenterTab; label: string; icon: string }> = [
  { key: 'participated', label: '我参与的', icon: '◎' },
  { key: 'mentioned', label: '@ 我的', icon: '@' },
  { key: 'todo', label: '待解决', icon: '?' },
];

export function CollabCenter(): JSX.Element {
  const activeTab = useCollabStore((s) => s.activeTab);
  const setActiveTab = useCollabStore((s) => s.setActiveTab);
  const selectedId = useCollabStore((s) => s.selectedContextId);
  const selectContext = useCollabStore((s) => s.selectContext);

  return (
    <div
      className="collab-center collab-active flex h-full w-full"
      style={{ backgroundColor: '#ffffff' }}
    >
      {/* 左栏：Tab + ContextList */}
      <div
        className="flex flex-shrink-0 flex-col"
        style={{
          width: 320,
          borderRight: '1px solid #e0e0e0',
          backgroundColor: '#f3f3f3',
        }}
      >
        {/* 顶部 Tab 栏 */}
        <div
          className="flex h-[35px] flex-shrink-0 items-center px-2"
          style={{ borderBottom: '1px solid #e0e0e0' }}
        >
          <span
            className="mr-2 px-2 py-0.5 text-2xs font-semibold uppercase tracking-wider"
            style={{ color: COLLAB_ACCENT, backgroundColor: 'rgba(99, 102, 241, 0.12)', borderRadius: 3 }}
          >
            💬 协作中心
          </span>
        </div>

        {/* Tab 切换 */}
        <div
          className="flex h-[32px] flex-shrink-0 items-stretch"
          style={{ borderBottom: '1px solid #e0e0e0' }}
        >
          {TABS.map((t) => {
            const active = t.key === activeTab;
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setActiveTab(t.key)}
                className="flex-1 text-2xs transition-colors"
                style={{
                  backgroundColor: active ? '#ffffff' : 'transparent',
                  color: active ? COLLAB_ACCENT : '#616161',
                  borderBottom: active ? `2px solid ${COLLAB_ACCENT}` : '2px solid transparent',
                  fontWeight: active ? 600 : 400,
                }}
              >
                <span className="mr-1">{t.icon}</span>
                {t.label}
              </button>
            );
          })}
        </div>

        {/* 列表 */}
        <div className="flex-1 overflow-hidden">
          <ContextList tab={activeTab} onSelect={(id) => selectContext(id)} />
        </div>
      </div>

      {/* 右栏：选中 context 的讨论详情 */}
      <div className="flex flex-1 flex-col overflow-hidden" style={{ minWidth: 0 }}>
        {selectedId ? (
          <TaskDiscussionPanel contextId={selectedId} />
        ) : (
          <div
            className="flex h-full flex-col items-center justify-center text-2xs"
            style={{ color: '#616161' }}
          >
            <div className="mb-2 text-3xl">💬</div>
            从左侧选择上下文查看讨论
          </div>
        )}
      </div>
    </div>
  );
}
