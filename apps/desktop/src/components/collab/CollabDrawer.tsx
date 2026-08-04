/**
 * CollabDrawer —— 任务页（审批 / 部署 / 热更）右侧 280px 讨论抽屉。
 *
 * 模式：
 *   - 关闭：open=false → translateX(100%) + pointer-events: none
 *   - 打开：open=true → translateX(0) + 占用 280px
 *   - 入场 / 离场 220ms ease-out
 *
 * 用途：
 *   - AuditDashboard 选中审批任务时挂载（仅该审批单有评论锚点时）
 *   - 未来 Phase 3B 部署任务详情 / Phase 3C 热更详情复用
 */
import { useCollabStore } from '@/store/collabStore';
import { TaskDiscussionPanel } from './TaskDiscussionPanel';

interface CollabDrawerProps {
  /** 强制显示（即使没评论锚点也展示"创建讨论"按钮） */
  fallbackCreate?: boolean;
}

export function CollabDrawer({ fallbackCreate }: CollabDrawerProps): JSX.Element {
  const contextId = useCollabStore((s) => s.drawerContextId);
  const close = useCollabStore((s) => s.closeDrawer);
  const contexts = useCollabStore((s) => s.contexts);

  const ctx = contextId ? contexts.find((c) => c.id === contextId) : null;
  const open = ctx !== null && ctx !== undefined;

  return (
    <>
      {/* 占位宽度（打开时撑开） */}
      <div
        style={{
          width: open ? 360 : 0,
          flexShrink: 0,
          transition: 'width 220ms cubic-bezier(0.4, 0, 0.2, 1)',
          overflow: 'hidden',
        }}
      />

      {/* 抽屉本体 */}
      <aside
        className="flex flex-shrink-0 flex-col border-l"
        style={{
          width: open ? 360 : 0,
          borderColor: '#d4d4d4',
          backgroundColor: '#ffffff',
          opacity: open ? 1 : 0,
          transition: 'width 220ms cubic-bezier(0.4, 0, 0.2, 1), opacity 180ms ease-out',
          pointerEvents: open ? 'auto' : 'none',
          overflow: 'hidden',
        }}
      >
        {open && ctx && (
          <>
            {/* 抽屉标题栏 */}
            <div
              className="flex h-[35px] flex-shrink-0 items-center justify-between border-b px-3 text-2xs"
              style={{
                backgroundColor: '#f3f3f3',
                borderColor: '#e0e0e0',
                color: '#333333',
              }}
            >
              <div className="flex items-center gap-2">
                <span style={{ color: '#6366f1' }}>💬</span>
                <span className="font-semibold uppercase tracking-wider">讨论</span>
                <span style={{ color: '#616161' }}>· {ctx.comment_count} 条</span>
              </div>
              <button
                type="button"
                onClick={close}
                className="rounded px-2 py-0.5 transition-colors hover:bg-vscode-border"
                style={{ color: '#616161' }}
                title="关闭 (Esc)"
              >
                ✕
              </button>
            </div>

            <TaskDiscussionPanel contextId={ctx.id} compact />
          </>
        )}
      </aside>

      {!open && fallbackCreate && (
        <div
          className="flex items-center justify-center p-2 text-2xs"
          style={{ color: '#616161' }}
        >
          <button
            type="button"
            onClick={() => {
              // V0 占位：弹个 alert 提示用户用协作中心创建锚点
              alert(
                '当前选中的任务尚无关联讨论上下文。\n\n请到「💬 协作中心」中创建锚点，或在代码行 / SQL 编辑器中右键"分享到讨论"。',
              );
            }}
            className="rounded border border-dashed px-3 py-1.5 transition-colors"
            style={{
              borderColor: '#d4d4d4',
              color: '#616161',
            }}
          >
            ＋ 发起讨论
          </button>
        </div>
      )}
    </>
  );
}
