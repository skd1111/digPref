/**
 * CommentThread —— 单条主评论 + 嵌套 Thread 回复（最多 3 层）。
 *
 * 行为：
 *   - 主评论显示头像 + 姓名 + 时间 + Markdown 内容 + Reaction + ⋯ 菜单（编辑 / 撤回）
 *   - 点击 "回复" 进入内联编辑框（CommentEditor）
 *   - 嵌套深度 = 当前评论 depth（最多 3 层，再深收起）
 *   - 5 分钟内的评论显示 "可撤回" / "可编辑" 标记
 */
import { useState } from 'react';
import { useCollabStore, formatRelativeTime } from '@/store/collabStore';
import { MarkdownRenderer } from './MarkdownRenderer';
import { ReactionPicker } from './ReactionPicker';
import { CommentEditor } from './CommentEditor';
import { CURRENT_USER, USER_BY_ID, type CollabComment, type ReactionEmoji } from '@/types/collab';

const MAX_DEPTH = 3;
const ENCRYPTED_PLACEHOLDER_RE = /^\[encrypted:[^\]]+\]$/;

function CommentBlock({
  comment,
  depth,
  contextId,
}: {
  comment: CollabComment;
  depth: number;
  contextId: string;
}): JSX.Element {
  const [replying, setReplying] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);

  const toggleReaction = useCollabStore((s) => s.toggleReaction);
  const editComment = useCollabStore((s) => s.editComment);
  const withdrawComment = useCollabStore((s) => s.withdrawComment);
  const comments = useCollabStore((s) => s.comments);
  const addComment = useCollabStore((s) => s.addComment);

  const author = USER_BY_ID[comment.author_id];
  const isMine = comment.author_id === CURRENT_USER.id;
  const isEncrypted = ENCRYPTED_PLACEHOLDER_RE.test(comment.content.trim());

  // 找子评论
  const children = comments
    .filter((c) => c.parent_id === comment.id)
    .sort((a, b) => a.created_at - b.created_at);

  const indent = depth * 20;

  return (
    <div className="flex flex-col" style={{ marginLeft: indent }}>
      <div
        className="rounded-md p-3"
        style={{
          backgroundColor: isMine ? 'rgba(99, 102, 241, 0.06)' : '#f3f3f3',
          border: `1px solid ${isMine ? 'rgba(99, 102, 241, 0.3)' : '#1f1f1f'}`,
        }}
      >
        {/* 头部：头像 + 姓名 + 时间 + ⋯ 菜单 */}
        <div className="mb-1.5 flex items-center gap-2">
          <span
            className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-2xs font-semibold text-white"
            style={{ backgroundColor: author?.avatar_color ?? '#616161' }}
          >
            {comment.author_name.charAt(0)}
          </span>
          <span className="text-2xs font-semibold" style={{ color: '#1f1f1f' }}>
            {comment.author_name}
          </span>
          <span className="text-2xs" style={{ color: '#616161' }}>
            · {formatRelativeTime(comment.created_at)}
            {comment.is_edited && ' · 已编辑'}
          </span>
          {isMine && (comment.can_edit || comment.can_withdraw) && (
            <div className="relative ml-auto">
              <button
                type="button"
                onClick={() => setMenuOpen((v) => !v)}
                className="rounded px-1 text-2xs transition-colors"
                style={{ color: '#616161' }}
                title="操作"
              >
                ⋯
              </button>
              {menuOpen && (
                <div
                  className="absolute right-0 top-full z-10 mt-1 w-32 rounded-md border py-1 text-2xs shadow-xl"
                  style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4' }}
                  onMouseLeave={() => setMenuOpen(false)}
                >
                  {comment.can_edit && (
                    <button
                      type="button"
                      onClick={() => {
                        setEditing(true);
                        setMenuOpen(false);
                      }}
                      className="block w-full px-3 py-1.5 text-left transition-colors hover:bg-gray-100"
                      style={{ color: '#1f1f1f' }}
                    >
                      ✏ 编辑
                    </button>
                  )}
                  {comment.can_withdraw && (
                    <button
                      type="button"
                      onClick={() => {
                        if (confirm('确认撤回？此操作不可撤销。')) {
                          withdrawComment(comment.id);
                        }
                        setMenuOpen(false);
                      }}
                      className="block w-full px-3 py-1.5 text-left transition-colors hover:bg-gray-100"
                      style={{ color: '#cd3131' }}
                    >
                      ↩ 撤回
                    </button>
                  )}
                  {(!comment.can_edit && !comment.can_withdraw) && (
                    <div className="px-3 py-1.5 text-2xs" style={{ color: '#616161' }}>
                      5 分钟内可操作
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 内容 */}
        {editing ? (
          <div className="space-y-2">
            <CommentEditor
              onSubmit={(content) => {
                editComment(comment.id, content);
                setEditing(false);
              }}
              onCancel={() => {
                setEditing(false);
              }}
              rows={2}
              autoFocus
            />
          </div>
        ) : (
          <MarkdownRenderer content={comment.content} isEncryptedPlaceholder={isEncrypted} />
        )}

        {/* Reaction */}
        {!editing && (
          <div className="mt-2">
            <ReactionPicker
              reactions={comment.reactions}
              onToggle={(emoji: ReactionEmoji) => toggleReaction(comment.id, emoji)}
            />
          </div>
        )}

        {/* 回复按钮 */}
        {!editing && depth < MAX_DEPTH - 1 && (
          <div className="mt-1.5">
            <button
              type="button"
              onClick={() => setReplying((v) => !v)}
              className="text-2xs transition-colors"
              style={{ color: '#616161' }}
            >
              {replying ? '取消回复' : '↩ 回复'}
            </button>
          </div>
        )}

        {/* 内联回复输入 */}
        {replying && (
          <div className="mt-2">
            <CommentEditor
              placeholder={`回复 @${comment.author_name}...`}
              rows={2}
              autoFocus
              onSubmit={(content, mentions) => {
                addComment({
                  contextId,
                  parentId: comment.id,
                  content,
                  mentions: Array.from(new Set([...mentions, comment.author_id])),
                });
                setReplying(false);
              }}
              onCancel={() => setReplying(false)}
            />
          </div>
        )}
      </div>

      {/* 递归子评论 */}
      {children.length > 0 && (
        <div className="mt-2 flex flex-col gap-2">
          {children.map((c) => (
            <CommentBlock
              key={c.id}
              comment={c}
              depth={Math.min(depth + 1, MAX_DEPTH - 1)}
              contextId={contextId}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface CommentThreadProps {
  contextId: string;
}

export function CommentThread({ contextId }: CommentThreadProps): JSX.Element {
  const comments = useCollabStore((s) => s.comments);

  const mainComments = comments
    .filter((c) => c.context_id === contextId && c.parent_id === null)
    .sort((a, b) => a.created_at - b.created_at);

  if (mainComments.length === 0) {
    return (
      <div
        className="flex items-center justify-center p-6 text-2xs"
        style={{ color: '#616161' }}
      >
        还没有评论，发表第一条吧 👇
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {mainComments.map((c) => (
        <CommentBlock key={c.id} comment={c} depth={0} contextId={contextId} />
      ))}
    </div>
  );
}
