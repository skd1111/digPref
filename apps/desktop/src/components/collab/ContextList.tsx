/**
 * ContextList —— 协作中心单 Tab 下的上下文列表。
 *
 * 每行：锚点类型徽章 + 标题 + 摘要 + 参与者头像 + 评论数 + 最后活动时间。
 * 单击：选中进入 TaskDiscussionPanel。
 */
import { useMemo, useState } from 'react';
import { useCollabStore, formatRelativeTime, selectByTab } from '@/store/collabStore';
import {
  ANCHOR_LABELS,
  STATUS_LABELS,
  USER_BY_ID,
  type CollabCenterTab,
  type CollabContext,
} from '@/types/collab';

interface ContextListProps {
  tab: CollabCenterTab;
  onSelect: (contextId: string) => void;
}

const TAB_LABELS: Record<CollabCenterTab, { label: string; hint: string; empty: string }> = {
  participated: {
    label: '我参与的',
    hint: '你创建 / 参与 / 被订阅的上下文',
    empty: '暂无参与的讨论。审批单 / 部署 / 代码行评论会自动出现在这里。',
  },
  mentioned: {
    label: '@ 我的',
    hint: '评论中 @ 了你（不论你最终是否回复）',
    empty: '暂无 @ 你的评论。',
  },
  todo: {
    label: '待解决',
    hint: '进行中 + 不是你创建的（需要你跟进）',
    empty: '暂无待解决项。所有任务你都跟进了 🎉',
  },
};

function ContextRow({
  ctx,
  selected,
  onClick,
}: {
  ctx: CollabContext;
  selected: boolean;
  onClick: () => void;
}): JSX.Element {
  const meta = ANCHOR_LABELS[ctx.anchor_type];
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full border-b px-3 py-2.5 text-left transition-colors"
      style={{
        borderColor: '#e0e0e0',
        backgroundColor: selected ? 'rgba(99, 102, 241, 0.14)' : 'transparent',
        borderLeft: selected ? '2px solid #6366f1' : '2px solid transparent',
      }}
    >
      <div className="flex items-start gap-2">
        <span
          className="flex-shrink-0 rounded px-1.5 py-0.5 text-2xs"
          style={{ backgroundColor: meta.color, color: '#0e0e0e', fontWeight: 600 }}
        >
          {meta.icon} {meta.label}
        </span>
        <div className="min-w-0 flex-1">
          <div
            className="truncate text-ui font-semibold"
            style={{ color: '#1f1f1f' }}
            title={ctx.title}
          >
            {ctx.title}
          </div>
          <div
            className="mt-0.5 truncate text-2xs"
            style={{ color: '#616161' }}
            title={ctx.summary}
          >
            {ctx.summary}
          </div>
          <div className="mt-1.5 flex items-center gap-1.5 text-2xs" style={{ color: '#616161' }}>
            <span>{ctx.target_env ? `[${ctx.target_env}]` : ''}</span>
            <span>·</span>
            <span>💬 {ctx.comment_count}</span>
            <span>·</span>
            <span>{formatRelativeTime(ctx.updated_at)}</span>
            {ctx.status === 'resolved' && (
              <>
                <span>·</span>
                <span style={{ color: '#059669' }}>✓ {STATUS_LABELS.resolved}</span>
              </>
            )}
            {ctx.status === 'archived' && (
              <>
                <span>·</span>
                <span style={{ color: '#616161' }}>🗄 {STATUS_LABELS.archived}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex -space-x-1.5">
          {ctx.participant_names.slice(0, 3).map((name, idx) => {
            const uid = ctx.participants[idx];
            const user = USER_BY_ID[uid];
            return (
              <span
                key={uid}
                title={name}
                className="flex h-5 w-5 items-center justify-center rounded-full text-2xs font-semibold text-white ring-1"
                style={{
                  backgroundColor: user?.avatar_color ?? '#616161',
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  boxShadow: '0 0 0 1px #d0d0d0' as any,
                }}
              >
                {name.charAt(0)}
              </span>
            );
          })}
          {ctx.participant_names.length > 3 && (
            <span
              className="flex h-5 w-5 items-center justify-center rounded-full text-2xs ring-1"
              style={{ backgroundColor: '#ececec', color: '#1f1f1f' }}
            >
              +{ctx.participant_names.length - 3}
            </span>
          )}
        </div>
      </div>
    </button>
  );
}

export function ContextList({ tab, onSelect }: ContextListProps): JSX.Element {
  const contexts = useCollabStore((s) => s.contexts);
  const selectedId = useCollabStore((s) => s.selectedContextId);
  const [search, setSearch] = useState('');

  const list = useMemo(() => {
    const filtered = selectByTab(tab);
    if (!search.trim()) return filtered;
    const q = search.toLowerCase();
    return filtered.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.summary.toLowerCase().includes(q) ||
        c.participant_names.some((n) => n.toLowerCase().includes(q)),
    );
  }, [tab, search, contexts]);

  const labels = TAB_LABELS[tab];

  return (
    <div className="flex h-full flex-col">
      <div
        className="flex-shrink-0 border-b p-3"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
      >
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
            {labels.label}
          </h2>
          <span className="text-2xs" style={{ color: '#616161' }}>
            {list.length} 项
          </span>
        </div>
        <p className="mb-2 text-2xs" style={{ color: '#616161' }}>
          {labels.hint}
        </p>
        <input
          type="text"
          placeholder="搜索标题 / 摘要 / 参与者..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded border px-2 py-1 text-2xs focus:outline-none"
          style={{
            backgroundColor: '#ffffff',
            borderColor: '#d4d4d4',
            color: '#1f1f1f',
          }}
        />
      </div>

      <div className="flex-1 overflow-auto">
        {list.length === 0 ? (
          <div
            className="flex h-full flex-col items-center justify-center p-6 text-center text-2xs"
            style={{ color: '#616161' }}
          >
            <div className="mb-2 text-3xl">💬</div>
            {labels.empty}
          </div>
        ) : (
          list.map((ctx) => (
            <ContextRow
              key={ctx.id}
              ctx={ctx}
              selected={ctx.id === selectedId}
              onClick={() => onSelect(ctx.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}
