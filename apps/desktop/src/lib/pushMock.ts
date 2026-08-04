/**
 * pushMock.ts —— Phase 9 协作推送 mock 模拟器（V0 前端，不接真实 WebSocket）。
 *
 * 行为：
 *   - 每 30-60 秒随机选一个 mock context 推送 1 条新评论
 *   - 推送内容由 mock 池随机抽取
 *   - 50% 概率 @ 当前用户（@zhangwei），触发 TopBar 红点
 *   - 启动后通过 startPushMock() 启动，stopPushMock() 停止（避免热更新泄漏）
 *
 * 后续 Phase 8 接入时：本文件整体替换为 useCollabStream hook（订阅 EVT.COLLAB_COMMENT_NEW）。
 */
import { useCollabStore } from '@/store/collabStore';
import { CURRENT_USER, MOCK_USERS, type CollabComment } from '@/types/collab';

const PUSH_INTERVAL_MIN_MS = 30_000;
const PUSH_INTERVAL_MAX_MS = 60_000;

const PUSH_POOL: Array<{
  contextIdx: number;     // index into MOCK_CONTEXTS（按 comment_count 倒序）
  authorIdx: number;      // MOCK_USERS index
  content: string;
  mentionsMe: boolean;
  reactionEmoji: '👍' | '👎' | '✅' | '❌' | '👀' | null;
}> = [
  // approval_ticket
  { contextIdx: 0, authorIdx: 1, content: '补充：回滚演练已跑通，staging 8 分钟。', mentionsMe: true, reactionEmoji: '✅' },
  { contextIdx: 0, authorIdx: 4, content: '14:00 时间点确认。', mentionsMe: false, reactionEmoji: null },
  // code_line
  { contextIdx: 1, authorIdx: 6, content: '已建 issue AUDIT-EVIDENCE-007，下个 sprint 一起补。', mentionsMe: true, reactionEmoji: '👀' },
  { contextIdx: 1, authorIdx: 4, content: '已订阅本上下文。', mentionsMe: false, reactionEmoji: null },
  // deploy_task
  { contextIdx: 2, authorIdx: 2, content: '50% → 100% 切换完成，错误率 0.02%。', mentionsMe: true, reactionEmoji: '✅' },
  { contextIdx: 2, authorIdx: 1, content: 'DB QPS 正常，未见异常。', mentionsMe: false, reactionEmoji: null },
  // log_segment
  { contextIdx: 3, authorIdx: 6, content: '已把 gateway-2 限流策略同步给网关团队。', mentionsMe: false, reactionEmoji: '👍' },
  // hotswap_task
  { contextIdx: 4, authorIdx: 7, content: '可以重新提交了，这次走完 MFA 即可。', mentionsMe: true, reactionEmoji: null },
  // sql_block
  { contextIdx: 5, authorIdx: 1, content: 'DDL 评审完成，明早 9:00 上 staging。', mentionsMe: true, reactionEmoji: '👍' },
];

function randomItem<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

let timer: ReturnType<typeof setTimeout> | null = null;
let running = false;

const getContexts = (): ReturnType<typeof useCollabStore.getState>['contexts'] =>
  useCollabStore.getState().contexts;

function schedule(): void {
  if (!running) return;
  const delay =
    PUSH_INTERVAL_MIN_MS +
    Math.random() * (PUSH_INTERVAL_MAX_MS - PUSH_INTERVAL_MIN_MS);
  timer = setTimeout(() => {
    fireOne();
    schedule();
  }, delay);
}

function fireOne(): void {
  const contexts = getContexts();
  if (contexts.length === 0) return;
  const p = randomItem(PUSH_POOL);
  const target = contexts[p.contextIdx % contexts.length];
  if (!target) return;
  const author = MOCK_USERS[p.authorIdx % MOCK_USERS.length];
  if (!author) return;

  const now = Date.now();
  const reactions: Record<string, '👍' | '👎' | '✅' | '❌' | '👀'> = {};
  if (p.reactionEmoji) {
    reactions[author.id] = p.reactionEmoji;
  }

  const mentions = p.mentionsMe ? [CURRENT_USER.id] : [];
  const c: CollabComment = {
    id: `c-push-${now}`,
    context_id: target.id,
    parent_id: null,
    author_id: author.id,
    author_name: author.name,
    content: p.content,
    content_hash: 'sha256:push-' + now.toString(16).padStart(8, '0'),
    mentions,
    reactions,
    is_edited: false,
    created_at: now,
    updated_at: null,
    can_withdraw: true,
    can_edit: true,
  };

  useCollabStore.setState((s) => ({
    comments: [...s.comments, c],
    contexts: s.contexts.map((ctx) =>
      ctx.id === target.id
        ? { ...ctx, comment_count: ctx.comment_count + 1, updated_at: now }
        : ctx,
    ),
  }));

  // eslint-disable-next-line no-console
  console.log('[collab.pushMock] 推送', author.name, '→', target.title);
}

/** 启动推送 mock（在用户首次打开协作中心时调用，避免启动时即推） */
export function startPushMock(): void {
  if (running) return;
  running = true;
  // 首次推延后 5-15 秒，让用户先看到静态内容
  timer = setTimeout(() => {
    fireOne();
    schedule();
  }, 5_000 + Math.random() * 10_000);
}

/** 停止推送 mock（Vite HMR / 卸载时清理） */
export function stopPushMock(): void {
  running = false;
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
}

/** 立即触发一次（用于"现在推送"按钮 / 单测） */
export function firePushNow(): void {
  fireOne();
}
