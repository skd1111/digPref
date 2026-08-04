/**
 * TaskDiscussionPanel —— 任务页右侧 / 协作中心中栏的"讨论详情"面板。
 *
 * 组成：标题 + 锚点信息 + 参与者列表 + CommentThread + 顶部 CommentEditor。
 */
import { useState } from 'react';
import { useCollabStore, formatRelativeTime } from '@/store/collabStore';
import { CommentThread } from './CommentThread';
import { CommentEditor } from './CommentEditor';
import {
  ANCHOR_LABELS,
  STATUS_LABELS,
  USER_BY_ID,
  type AnchorType,
} from '@/types/collab';
import { ShareDialog } from './ShareDialog';

interface TaskDiscussionPanelProps {
  contextId: string;
  /** 是否在右侧抽屉（collapses 标题区） */
  compact?: boolean;
}

function formatAnchor(anchorType: AnchorType, payload: Record<string, unknown>): string {
  if (anchorType === 'code_line') {
    return `${payload['file']}:L${payload['line']}`;
  }
  if (anchorType === 'log_segment') {
    return `${payload['file']}:L${payload['start_line']}-L${payload['end_line']}`;
  }
  if (anchorType === 'approval_ticket') {
    return `${payload['ticket_id']}`;
  }
  if (anchorType === 'deploy_task') {
    return `${payload['deploy_id']}`;
  }
  if (anchorType === 'hotswap_task') {
    return `${payload['task_id']}`;
  }
  if (anchorType === 'sql_block') {
    return `${payload['sql_id']} v${payload['version']}`;
  }
  return '自定义上下文';
}

export function TaskDiscussionPanel({ contextId, compact }: TaskDiscussionPanelProps): JSX.Element {
  const ctx = useCollabStore((s) => s.contexts.find((c) => c.id === contextId));
  const addComment = useCollabStore((s) => s.addComment);
  const markResolved = useCollabStore((s) => s.markContextResolved);
  const markActive = useCollabStore((s) => s.markContextActive);
  const archiveContext = useCollabStore((s) => s.archiveContext);
  const [showShare, setShowShare] = useState(false);

  if (!ctx) {
    return (
      <div
        className="flex h-full items-center justify-center p-6 text-center text-2xs"
        style={{ color: '#616161' }}
      >
        ← 从左侧选择上下文查看讨论
      </div>
    );
  }

  const meta = ANCHOR_LABELS[ctx.anchor_type];

  return (
    <div className="flex h-full flex-col">
      {/* 标题区 */}
      <div
        className="flex-shrink-0 border-b p-3"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
      >
        <div className="mb-1.5 flex items-center gap-2">
          <span
            className="rounded px-1.5 py-0.5 text-2xs font-semibold"
            style={{ backgroundColor: meta.color, color: '#0e0e0e' }}
          >
            {meta.icon} {meta.label}
          </span>
          <span className="text-2xs" style={{ color: '#616161' }}>
            {ctx.target_env ? `[${ctx.target_env}]` : ''}
            {formatAnchor(ctx.anchor_type, ctx.anchor_payload)}
          </span>
          <span className="ml-auto text-2xs" style={{ color: '#616161' }}>
            {STATUS_LABELS[ctx.status]}
          </span>
        </div>
        {!compact && (
          <h2 className="text-base font-semibold" style={{ color: '#1f1f1f' }}>
            {ctx.title}
          </h2>
        )}
        <div className="mt-1 text-2xs" style={{ color: '#a0a0a0' }}>
          {ctx.summary}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <div className="flex -space-x-1.5">
            {ctx.participant_names.map((name, idx) => {
              const uid = ctx.participants[idx];
              const u = USER_BY_ID[uid];
              return (
                <span
                  key={uid}
                  title={name}
                  className="flex h-6 w-6 items-center justify-center rounded-full text-2xs font-semibold text-white"
                  style={{
                    backgroundColor: u?.avatar_color ?? '#616161',
                    boxShadow: '0 0 0 2px #d0d0d0',
                  }}
                >
                  {name.charAt(0)}
                </span>
              );
            })}
          </div>
          <span className="text-2xs" style={{ color: '#616161' }}>
            {ctx.participant_names.length} 参与者 · 💬 {ctx.comment_count} · {formatRelativeTime(ctx.updated_at)}
          </span>
          <div className="ml-auto flex gap-1">
            <button
              type="button"
              onClick={() => setShowShare(true)}
              className="rounded px-2 py-0.5 text-2xs transition-colors"
              style={{
                backgroundColor: 'transparent',
                color: '#4f46e5',
                border: '1px solid #6366f1',
              }}
              title="分享到企微 / 钉钉 / 飞书"
            >
              📤 分享
            </button>
            {ctx.status === 'active' && (
              <button
                type="button"
                onClick={() => markResolved(ctx.id)}
                className="rounded px-2 py-0.5 text-2xs transition-colors"
                style={{
                  backgroundColor: 'rgba(78, 201, 176, 0.12)',
                  color: '#059669',
                  border: '1px solid #4ec9b0',
                }}
              >
                ✓ 标记已解决
              </button>
            )}
            {ctx.status === 'resolved' && (
              <button
                type="button"
                onClick={() => markActive(ctx.id)}
                className="rounded px-2 py-0.5 text-2xs transition-colors"
                style={{
                  backgroundColor: 'transparent',
                  color: '#616161',
                  border: '1px solid #d4d4d4',
                }}
              >
                ↩ 重新打开
              </button>
            )}
            {ctx.status !== 'archived' && (
              <button
                type="button"
                onClick={() => {
                  if (confirm('归档后该讨论会折叠到"已归档"分类，确认？')) {
                    archiveContext(ctx.id);
                  }
                }}
                className="rounded px-2 py-0.5 text-2xs transition-colors"
                style={{
                  backgroundColor: 'transparent',
                  color: '#616161',
                  border: '1px solid #d4d4d4',
                }}
                title="归档"
              >
                🗄
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 评论流 */}
      <div className="flex-1 overflow-auto p-3">
        <CommentThread contextId={contextId} />
      </div>

      {/* 顶部编辑器 */}
      <div
        className="flex-shrink-0 border-t p-3"
        style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff' }}
      >
        <CommentEditor
          onSubmit={(content, mentions) => {
            addComment({
              contextId,
              parentId: null,
              content,
              mentions,
            });
          }}
          rows={2}
        />
      </div>

      {showShare && <ShareDialog contextId={contextId} onClose={() => setShowShare(false)} />}
    </div>
  );
}
