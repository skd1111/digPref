/**
 * SessionInlineView —— BUGFIX #67 会话只读浏览视图（嵌入 center 区域）。
 *
 * 修复前 WorkspaceLayout 在 `active === 'sessions'` 时用 `<Outlet />` 渲染 center，
 * 但 router.tsx 没注册 `/sessions/:id` 子路由 → `<Outlet />` 回落到 HomeView →
 * chatStore 没有该会话 tab → CenterChatFlow 一直显示欢迎页（"没有渲染聊天记录"）。
 *
 * 修复后：SessionsPanel.onClick 设置 store.activeSessionId，
 * 本组件订阅 activeSessionId，主动调 sessionsStore.get() 拉详情，
 * 把消息列表嵌入到 center 区域（非模态）。
 *
 * 关键交互：
 *   - 顶部会话标题 + 元信息 + 「详情」按钮（打开 SessionDetailDialog 模态）
 *   - 中间消息流（只读，复用 SessionDetailDialog messages Tab 的渲染逻辑）
 *   - 空状态：未选中会话时显示提示
 *
 * 遵循约定：
 *   - 不重写消息过滤（`!content.startsWith('（mock')`）—— 与 SessionDetailDialog 保持一致
 *   - 复用 SessionDetailDialog 的打开方式（`open + sessionId` Props）
 *   - 通过 `detail?.id === sessionId` 校验避免 stale-closure
 */
import { useEffect, useState } from 'react';
import { useSessionsStore } from '@/store/sessionsStore';
import { SessionDetailDialog } from './SessionDetailDialog';

export function SessionInlineView(): JSX.Element {
  const activeId = useSessionsStore((s) => s.activeSessionId);
  const activeDetail = useSessionsStore((s) => s.activeSessionDetail);
  const error = useSessionsStore((s) => s.error);
  const get = useSessionsStore((s) => s.get);

  // 「详情」模态开关（嵌入视图右上角）
  const [showDetailDialog, setShowDetailDialog] = useState(false);

  // 切换会话时拉详情（注意：activeId 变化即触发，复用 sessionsStore.get 的去重）
  useEffect(() => {
    if (activeId) {
      void get(activeId);
    }
  }, [activeId, get]);

  // 空状态 1：没选会话
  if (!activeId) {
    return (
      <div className="flex h-full items-center justify-center" style={{ color: '#9ca3af' }}>
        <div className="text-center">
          <div className="mb-2 text-lg">📂 会话浏览</div>
          <div className="text-xs">从左侧选择会话查看历史消息</div>
        </div>
      </div>
    );
  }

  // stale-closure 防护：只有当 detail.id 与当前 activeId 匹配时才用 detail
  const detail = activeDetail?.id === activeId ? activeDetail : null;
  const meta = detail
    ? {
        title: detail.title,
        owner: detail.owner,
        created_at: detail.created_at,
        updated_at: detail.updated_at,
        is_branch: Boolean(detail.parent_session_id),
      }
    : null;

  // 过滤 mock 消息（与 SessionDetailDialog 保持一致）
  const messages = (detail?.messages ?? [])
    .filter((m) => !String(m.content ?? '').startsWith('（mock'));

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* 头部 */}
      <div
        className="flex items-center justify-between border-b px-4 py-2"
        style={{ borderBottom: '1px solid #e0e0e0', backgroundColor: '#fafafa' }}
      >
        <div>
          <h2 className="text-sm font-semibold" style={{ color: '#202124' }}>
            {meta?.title ?? '加载中…'}
          </h2>
          <div className="mt-0.5 text-xs" style={{ color: '#6b7280' }}>
            {meta?.owner ? `${meta.owner} · ` : ''}
            {meta?.is_branch ? '🔀 分支会话 · ' : ''}
            {meta?.created_at
              ? `创建于 ${new Date(meta.created_at).toLocaleString()}`
              : ''}
            {meta?.updated_at && meta.updated_at > meta.created_at
              ? ` · 更新于 ${new Date(meta.updated_at).toLocaleString()}`
              : ''}
          </div>
        </div>
        <button
          type="button"
          onClick={() => setShowDetailDialog(true)}
          className="rounded px-3 py-1 text-xs hover:opacity-90"
          style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
          title="打开完整详情（统计 / 分支 / 共享 / 事件链）"
        >
          详情 →
        </button>
      </div>

      {/* 错误提示（用户说"没渲染聊天记录"可能是因为 get() 失败被吞） */}
      {error && (
        <div
          className="px-4 py-2 text-xs"
          style={{ backgroundColor: '#fdeaea', color: '#cd3131', borderBottom: '1px solid #e0e0e0' }}
        >
          ⚠️ {error}
        </div>
      )}

      {/* 消息流 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {!detail ? (
          <div className="flex h-full items-center justify-center text-xs" style={{ color: '#9ca3af' }}>
            加载会话详情…
          </div>
        ) : messages.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs" style={{ color: '#9ca3af' }}>
            暂无消息
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-2">
            {messages.map((m, idx) => (
              <div
                key={idx}
                className="rounded p-2 text-xs"
                style={{ backgroundColor: '#f3f3f3', border: '1px solid #c0c0c0' }}
              >
                <div className="mb-1 flex justify-between" style={{ color: '#616161' }}>
                  <span className="font-mono">{String(m.role)}</span>
                  <span>{new Date(Number(m.created_at)).toLocaleString()}</span>
                </div>
                <div className="whitespace-pre-wrap break-words">{String(m.content)}</div>
                {Boolean(m.tool_name) && (
                  <div className="mt-1" style={{ color: '#0b6bcb' }}>
                    🔧 {String(m.tool_name)}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 完整详情模态（统计 / 分支 / 共享 / 事件链） */}
      <SessionDetailDialog
        sessionId={activeId}
        open={showDetailDialog}
        onClose={() => setShowDetailDialog(false)}
      />
    </div>
  );
}