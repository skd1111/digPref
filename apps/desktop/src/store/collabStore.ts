/**
 * collabStore —— Phase 9 任务级协作引擎状态（V0 MVP，前端 mock 数据）。
 *
 * 范围：
 *   - 7 类锚点（context）列表
 *   - 评论 + Thread 嵌套回复 + Reaction
 *   - 协作中心当前 Tab + 选中 context
 *   - 当前用户未读 @ 提醒数（持久化到 uiStore）
 *   - 5 分钟撤回 / 编辑窗口判定
 *   - 推送 mock 队列
 *
 * 后续接入 Phase 8 Server 时：
 *   - mock 数据替换为 REST 拉取（GET /collab/contexts + GET /collab/comments）
 *   - pushMock 替换为 WebSocket 订阅 `collab:*` 事件
 *   - zustand 接口保持兼容即可平滑切换
 */
import { create } from 'zustand';
import {
  CURRENT_USER,
  MOCK_USERS,
  USER_BY_ID,
  type CollabCenterTab,
  type CollabComment,
  type CollabContext,
  type ContextStatus,
  type DeepLinkToken,
  type ReactionEmoji,
  type ReactionMap,
} from '@/types/collab';

// ---------- 时间工具 ----------

const NOW = Date.now();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
const WITHDRAW_WINDOW_MS = 5 * MIN;

function withinEditWindow(createdAt: number): boolean {
  return Date.now() - createdAt <= WITHDRAW_WINDOW_MS;
}

function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 1 * MIN) return '刚刚';
  if (diff < 1 * HOUR) return `${Math.floor(diff / MIN)} 分钟前`;
  if (diff < 1 * DAY) return `${Math.floor(diff / HOUR)} 小时前`;
  return `${Math.floor(diff / DAY)} 天前`;
}

// ---------- Mock 锚点 + 评论 ----------

interface CommentSeed {
  author: string;            // user_id
  content: string;
  /** 距今的毫秒数（正数=过去） */
  ageMs: number;
  /** 嵌套深度：0=主评论, 1=一层回复, 2=二层回复（最多 3 层） */
  depth?: number;
  mentions?: string[];
  reactions?: Array<[string, ReactionEmoji]>;
}

const sha256Stub = (s: string): string =>
  'sha256:' +
  Array.from(s)
    .reduce((acc, c) => (acc * 31 + c.charCodeAt(0)) >>> 0, 7)
    .toString(16)
    .padStart(8, '0') +
  Array.from(s)
    .reverse()
    .reduce((acc, c) => (acc * 17 + c.charCodeAt(0)) >>> 0, 11)
    .toString(16)
    .padStart(8, '0');

const buildComments = (
  contextId: string,
  seeds: CommentSeed[],
  baseTime: number,
): CollabComment[] => {
  const ordered = seeds.map((s, idx) => {
    const createdAt = baseTime - s.ageMs + idx * 1000; // 避免同毫秒
    const c: CollabComment = {
      id: `c-${contextId}-${idx + 1}`,
      context_id: contextId,
      parent_id: null,
      author_id: s.author,
      author_name: USER_BY_ID[s.author]?.name ?? s.author,
      content: s.content,
      content_hash: sha256Stub(s.content),
      mentions: s.mentions ?? [],
      reactions: Object.fromEntries(s.reactions ?? []) as ReactionMap,
      is_edited: false,
      created_at: createdAt,
      updated_at: null,
      can_withdraw: withinEditWindow(createdAt),
      can_edit: withinEditWindow(createdAt),
    };
    return c;
  });
  // 简单的 parent_id 串接：每个非 0 深度指向当前 depth-1 的最近主评论
  const lastMain: CollabComment[] = [];
  ordered.forEach((c, i) => {
    const seed = seeds[i];
    const depth = seed.depth ?? 0;
    if (depth === 0) {
      lastMain.length = 0;
      lastMain.push(c);
    } else if (lastMain.length > 0) {
      c.parent_id = lastMain[lastMain.length - 1].id;
    }
  });
  return ordered;
};

// 7 个 mock 锚点，贴近真实金融场景
const MOCK_CONTEXTS: CollabContext[] = [
  {
    id: 'approval_ticket:tkt-sql-prod-2026-07-09-001',
    anchor_type: 'approval_ticket',
    anchor_payload: { ticket_id: 'tkt-sql-prod-2026-07-09-001' },
    target_env: 'prod',
    related_ticket_id: 'tkt-sql-prod-2026-07-09-001',
    title: '订单库慢查询上线（prod 200 行 UPDATE）',
    summary: '李娜 (DBA) 提交：修复 11/03 促销订单对账差异，预计影响 200 行 + 资金 128 万',
    created_by: 'u-lina',
    created_by_name: '李娜',
    created_at: NOW - 6 * HOUR,
    updated_at: NOW - 12 * MIN,
    status: 'active',
    participants: ['u-lina', 'u-shen', 'u-zhangwei', 'u-zhaomin', 'u-mingyu'],
    participant_names: ['李娜', '沈雷', '张伟', '赵敏', '王明宇'],
    comment_count: 6,
  },
  {
    id: 'code_line:apps/desktop/src/store/auditStore.ts:L45',
    anchor_type: 'code_line',
    anchor_payload: { file: 'apps/desktop/src/store/auditStore.ts', line: 45, commit: 'a1b2c3d' },
    target_env: 'dev',
    title: 'auditStore MOCK 证据派生逻辑',
    summary: '王磊 (研发) 提问：MOCK_EVIDENCE 派生规则是否覆盖所有 status？',
    created_by: 'u-wanglei',
    created_by_name: '王磊',
    created_at: NOW - 1 * DAY,
    updated_at: NOW - 3 * HOUR,
    status: 'active',
    participants: ['u-wanglei', 'u-zhangwei', 'u-zhaolin'],
    participant_names: ['王磊', '张伟', '赵琳'],
    comment_count: 4,
  },
  {
    id: 'deploy_task:deploy-ordersvc-prod-2026-07-10',
    anchor_type: 'deploy_task',
    anchor_payload: { deploy_id: 'deploy-ordersvc-prod-2026-07-10' },
    target_env: 'prod',
    title: '订单服务 v2.6 prod 灰度发布',
    summary: '王明宇 (SRE) 启动 10% → 50% → 100% 三段灰度，需 DBA + 业务值班',
    created_by: 'u-mingyu',
    created_by_name: '王明宇',
    created_at: NOW - 4 * HOUR,
    updated_at: NOW - 8 * MIN,
    status: 'active',
    participants: ['u-mingyu', 'u-shen', 'u-lina', 'u-zhangwei'],
    participant_names: ['王明宇', '沈雷', '李娜', '张伟'],
    comment_count: 5,
  },
  {
    id: 'log_segment:app.log:L1234-L1280',
    anchor_type: 'log_segment',
    anchor_payload: { file: 'app.log', start_line: 1234, end_line: 1280 },
    target_env: 'prod',
    title: '支付通道 TimeoutException 异常堆栈',
    summary: '11/08 21:34-21:38 期间 4 次超时，需要确认上游网关是否有限流',
    created_by: 'u-mingyu',
    created_by_name: '王明宇',
    created_at: NOW - 8 * HOUR,
    updated_at: NOW - 5 * HOUR,
    status: 'active',
    participants: ['u-mingyu', 'u-zhaolin'],
    participant_names: ['王明宇', '赵琳'],
    comment_count: 3,
  },
  {
    id: 'hotswap_task:hot-pay-gateway-2026-07-08',
    anchor_type: 'hotswap_task',
    anchor_payload: { task_id: 'hot-pay-gateway-2026-07-08' },
    target_env: 'prod',
    title: '支付网关 RefundProcessor 空指针热更',
    summary: 'Arthas retransform 热更（被驳回 1 次后重新提交，已通过字节码结构校验）',
    created_by: 'u-zhaolin',
    created_by_name: '赵琳',
    created_at: NOW - 1.5 * DAY,
    updated_at: NOW - 6 * HOUR,
    status: 'resolved',
    participants: ['u-zhaolin', 'u-shen', 'u-mingyu'],
    participant_names: ['赵琳', '沈雷', '王明宇'],
    comment_count: 5,
  },
  {
    id: 'sql_block:analysis-q3-2026-w28',
    anchor_type: 'sql_block',
    anchor_payload: { sql_id: 'analysis-q3-2026-w28', version: 2 },
    target_env: 'staging',
    title: 'Q3 经营分析 SQL 评审（v2 草稿）',
    summary: '陈静 (数据) 提交：按产品线拆解 GMV 同比，需要 DBA 协助索引建议',
    created_by: 'u-chenjing',
    created_by_name: '陈静',
    created_at: NOW - 2 * HOUR,
    updated_at: NOW - 25 * MIN,
    status: 'active',
    participants: ['u-chenjing', 'u-lina'],
    participant_names: ['陈静', '李娜'],
    comment_count: 2,
  },
  {
    id: 'custom:custom-team-announce-2026-07-01',
    anchor_type: 'custom',
    anchor_payload: { type: 'announcement' },
    title: '研发部 7 月月报 - 协作 / 合规 / 稳定性',
    summary: '本月协作 312 条讨论、审批 47 次、零 P0 故障',
    created_by: 'u-zhangwei',
    created_by_name: '张伟',
    created_at: NOW - 5 * DAY,
    updated_at: NOW - 4 * DAY,
    status: 'archived',
    participants: ['u-zhangwei'],
    participant_names: ['张伟'],
    comment_count: 2,
  },
];

// 评论 mock seed
const COMMENT_SEEDS: Record<string, CommentSeed[]> = {
  'approval_ticket:tkt-sql-prod-2026-07-09-001': [
    {
      author: 'u-lina',
      ageMs: 5.8 * HOUR,
      content: '申请依据：11/03 促销订单 t_order 200 条 status=paid 但 t_payment 缺记录。**UPDATE 会清空 payment_id**，请审批专家审核。',
      mentions: ['u-shen'],
      reactions: [['u-shen', '👀']],
    },
    {
      author: 'u-shen',
      ageMs: 5.5 * HOUR,
      content: '收到。我有几个问题：\n\n1. payment_id 清空后是否会影响后续对账？\n2. 200 行的选取依据是什么？\n3. 有没有 rollback 计划？',
      depth: 1,
      mentions: ['u-lina'],
      reactions: [['u-lina', '✅']],
    },
    {
      author: 'u-lina',
      ageMs: 5.3 * HOUR,
      content: '@沈雷 1. payment_id 清空后会用 t_payment_reconcile 重建；2. 见 `IN (SELECT order_id FROM t_payment_reconcile WHERE status=\'orphan\')`；3. 完整 backup + binlog 切分恢复预案已附。',
      depth: 2,
      mentions: ['u-shen'],
    },
    {
      author: 'u-zhangwei',
      ageMs: 4 * HOUR,
      content: '建议 **先在 staging 跑 1 次**全量回放，确认回滚点再上 prod。',
      depth: 1,
      reactions: [['u-lina', '👍'], ['u-mingyu', '👍']],
    },
    {
      author: 'u-zhaomin',
      ageMs: 2 * HOUR,
      content: '财务侧已确认：t_payment_reconcile 表数据完整，200 笔差异可追溯。',
    },
    {
      author: 'u-shen',
      ageMs: 12 * MIN,
      content: '@所有人 批准通过。请 DBA 在 14:00 业务低峰执行，运维同步监控。',
      reactions: [['u-lina', '👍'], ['u-zhangwei', '👍'], ['u-mingyu', '👍']],
    },
  ],
  'code_line:apps/desktop/src/store/auditStore.ts:L45': [
    {
      author: 'u-wanglei',
      ageMs: 22 * HOUR,
      content: '这段 `MOCK_EVIDENCE` 派生逻辑只对 `reviewed_at` 存在的情况加了 mfa_pass + 审批事件。**expired 状态没覆盖**，是不是 bug？',
      reactions: [['u-zhangwei', '👀']],
    },
    {
      author: 'u-zhangwei',
      ageMs: 21 * HOUR,
      content: '@王磊 不是 bug，是 MVP 占位。expired 状态后续会在 `services/agent/src/agent/audit/tickets.py::expire_stale_tasks` 触发时单独加 evidence。当前 mock 不模拟过期。',
      depth: 1,
      reactions: [['u-wanglei', '✅']],
    },
    {
      author: 'u-wanglei',
      ageMs: 20 * HOUR,
      content: '明白了。那可以提个 issue 跟踪吗？',
      depth: 1,
    },
    {
      author: 'u-zhaolin',
      ageMs: 3 * HOUR,
      content: '已记录，Phase 5 真实后端接入时一起补：`AUDIT-EVIDENCE-007` "覆盖 expired / delegated 状态的 evidence 派生"。',
      depth: 1,
    },
  ],
  'deploy_task:deploy-ordersvc-prod-2026-07-10': [
    {
      author: 'u-mingyu',
      ageMs: 3.5 * HOUR,
      content: '🚀 启动 10% 灰度，5 台机器先滚动更新。',
      reactions: [['u-lina', '👀']],
    },
    {
      author: 'u-lina',
      ageMs: 3 * HOUR,
      content: 'DB 端已准备好，连接池监控会盯住，QPS > 500 报警。',
      depth: 1,
    },
    {
      author: 'u-mingyu',
      ageMs: 2.5 * HOUR,
      content: '10% 灰度 30 分钟无异常 → 50%。',
    },
    {
      author: 'u-shen',
      ageMs: 1.5 * HOUR,
      content: '50% 阶段监控一下错误率，超过 0.5% 立即回滚。',
      depth: 1,
      reactions: [['u-mingyu', '👍']],
    },
    {
      author: 'u-mingyu',
      ageMs: 8 * MIN,
      content: '100% 灰度完成，进入 1 小时观察期。错误率 0.02%，延迟 P99 +3ms 可接受。',
      reactions: [['u-shen', '✅']],
    },
  ],
  'log_segment:app.log:L1234-L1280': [
    {
      author: 'u-mingyu',
      ageMs: 7.5 * HOUR,
      content: '这 4 次 `TimeoutException` 都集中在 21:34-21:38，4 分钟。怀疑是上游 `payment-gateway-2` 的限流。',
    },
    {
      author: 'u-zhaolin',
      ageMs: 7 * HOUR,
      content: '@王明宇 看了，gateway-2 的 circuit breaker 在 21:34 触发了一次（连续 5xx），21:38 自动恢复。EAIDE 这边的 TimeoutException 是连带反应。',
      depth: 1,
      reactions: [['u-mingyu', '✅']],
    },
    {
      author: 'u-mingyu',
      ageMs: 5 * HOUR,
      content: '那根因是 gateway-2 的限流策略太敏感？这个需要跟网关团队提。',
      depth: 1,
    },
  ],
  'hotswap_task:hot-pay-gateway-2026-07-08': [
    {
      author: 'u-zhaolin',
      ageMs: 30 * HOUR,
      content: '热更申请：`RefundProcessor.calcFee()` 增加空值短路，避免 amount=0 时 NPE。',
      reactions: [['u-shen', '👀']],
    },
    {
      author: 'u-shen',
      ageMs: 28 * HOUR,
      content: '@赵琳 字节码结构校验必须先过。生产环境对热更要求最严格。',
      depth: 1,
    },
    {
      author: 'u-zhaolin',
      ageMs: 26 * HOUR,
      content: '已过校验：方法签名未变，字段未变，父类接口未变。',
      depth: 1,
    },
    {
      author: 'u-shen',
      ageMs: 24 * HOUR,
      content: '驳回。需要补完整压测报告 + 至少 1 小时 staging 观察。',
      reactions: [['u-zhaolin', '👎']],
    },
    {
      author: 'u-mingyu',
      ageMs: 6 * HOUR,
      content: 'staging 跑 2 小时，错误率 0，延迟无变化。重新提交。',
    },
  ],
  'sql_block:analysis-q3-2026-w28': [
    {
      author: 'u-chenjing',
      ageMs: 1.8 * HOUR,
      content: 'Q3 经营分析 SQL v2：按产品线拆解 GMV 同比，跑了 3.2 秒，需要索引优化。',
    },
    {
      author: 'u-lina',
      ageMs: 25 * MIN,
      content: '@陈静 加联合索引 `(product_line, order_date, status)` 应该能降到 0.3 秒。明天给你 DDL 评审。',
      reactions: [['u-chenjing', '👍']],
    },
  ],
  'custom:custom-team-announce-2026-07-01': [
    {
      author: 'u-zhangwei',
      ageMs: 4.5 * DAY,
      content: '本月协作 312 条讨论、审批 47 次、零 P0 故障。✅ 大家辛苦。',
    },
    {
      author: 'u-zhangwei',
      ageMs: 4 * DAY,
      content: '**下一步重点**：Phase 9 任务级协作引擎（行级评论 + IM 分享桥）将在本月内启动。',
      depth: 1,
    },
  ],
};

const MOCK_COMMENTS: CollabComment[] = MOCK_CONTEXTS.flatMap((c) =>
  buildComments(c.id, COMMENT_SEEDS[c.id] ?? [], c.created_at),
);

// ---------- Deep Link mock ----------

const MOCK_DEEPLINKS: DeepLinkToken[] = MOCK_CONTEXTS.slice(0, 3).map((c, i) => ({
  token: `mock-dl-${c.id.slice(-8)}-${i}`,
  context_id: c.id,
  user_id: CURRENT_USER.id,
  created_at: NOW - i * HOUR,
  expires_at: NOW - i * HOUR + 5 * MIN,
  consumed_at: null,
}));

// ---------- 协作中心派生数据 ----------

interface CollabState {
  // 数据
  contexts: CollabContext[];
  comments: CollabComment[];
  deeplinks: DeepLinkToken[];

  // UI 状态
  activeTab: CollabCenterTab;
  selectedContextId: string | null;
  // 当前用户在哪个页面打开的任务讨论抽屉（按 context_id 索引；关闭时 null）
  drawerContextId: string | null;

  // 模拟实时推送队列
  pushQueue: Array<{ contextId: string; comment: CollabComment; delayMs: number }>;

  // Actions
  setActiveTab: (tab: CollabCenterTab) => void;
  selectContext: (id: string | null) => void;
  openDrawer: (contextId: string) => void;
  closeDrawer: () => void;

  addComment: (input: {
    contextId: string;
    parentId: string | null;
    content: string;
    mentions: string[];
  }) => CollabComment;

  editComment: (commentId: string, content: string) => void;
  withdrawComment: (commentId: string) => void;
  toggleReaction: (commentId: string, emoji: ReactionEmoji) => void;

  markContextResolved: (contextId: string) => void;
  markContextActive: (contextId: string) => void;
  archiveContext: (contextId: string) => void;

  // Deep Link
  generateDeepLink: (contextId: string) => string;
  consumeDeepLink: (token: string) => string | null;
}

// ---------- 工具函数 ----------

const getCommentChildren = (
  comments: CollabComment[],
  parentId: string,
): CollabComment[] =>
  comments
    .filter((c) => c.parent_id === parentId)
    .sort((a, b) => a.created_at - b.created_at);

const isMyContext = (ctx: CollabContext, userId: string): boolean =>
  ctx.created_by === userId || ctx.participants.includes(userId);

const isMentionedToMe = (ctx: CollabContext, comments: CollabComment[], userId: string): boolean =>
  comments.some(
    (c) => c.context_id === ctx.id && c.mentions.includes(userId) && c.author_id !== userId,
  );

const isTodo = (ctx: CollabContext, _comments: CollabComment[], userId: string): boolean =>
  ctx.status === 'active' && ctx.created_by !== userId;

export const useCollabStore = create<CollabState>((set, get) => ({
  contexts: MOCK_CONTEXTS,
  comments: MOCK_COMMENTS,
  deeplinks: MOCK_DEEPLINKS,
  activeTab: 'participated',
  selectedContextId: MOCK_CONTEXTS[0]?.id ?? null,
  drawerContextId: null,
  pushQueue: [],

  setActiveTab: (tab) => set({ activeTab: tab }),
  selectContext: (id) => set({ selectedContextId: id }),
  openDrawer: (contextId) => set({ drawerContextId: contextId }),
  closeDrawer: () => set({ drawerContextId: null }),

  addComment: ({ contextId, parentId, content, mentions }) => {
    const now = Date.now();
    const me = CURRENT_USER;
    const c: CollabComment = {
      id: `c-${contextId}-${now}`,
      context_id: contextId,
      parent_id: parentId,
      author_id: me.id,
      author_name: me.name,
      content,
      content_hash: sha256Stub(content),
      mentions,
      reactions: {},
      is_edited: false,
      created_at: now,
      updated_at: null,
      can_withdraw: true,
      can_edit: true,
    };
    set((s) => ({
      comments: [...s.comments, c],
      contexts: s.contexts.map((ctx) =>
        ctx.id === contextId
          ? { ...ctx, comment_count: ctx.comment_count + 1, updated_at: now }
          : ctx,
      ),
    }));
    // TODO: console.log('[AUDIT] collab.comment.add', ...) —— 后端 Phase 8 接入时替换
    // eslint-disable-next-line no-console
    console.log(
      '[collab] comment.add',
      JSON.stringify({ contextId, parentId, mentions, length: content.length }),
    );
    return c;
  },

  editComment: (commentId, content) => {
    const now = Date.now();
    set((s) => ({
      comments: s.comments.map((c) =>
        c.id === commentId && c.can_edit
          ? { ...c, content, content_hash: sha256Stub(content), is_edited: true, updated_at: now }
          : c,
      ),
    }));
  },

  withdrawComment: (commentId) => {
    set((s) => ({
      comments: s.comments.filter((c) => !(c.id === commentId && c.can_withdraw)),
      contexts: s.contexts.map((ctx) => {
        const target = s.comments.find((cm) => cm.id === commentId);
        if (target && target.context_id === ctx.id) {
          return { ...ctx, comment_count: Math.max(0, ctx.comment_count - 1) };
        }
        return ctx;
      }),
    }));
  },

  toggleReaction: (commentId, emoji) => {
    const me = CURRENT_USER;
    set((s) => ({
      comments: s.comments.map((c) => {
        if (c.id !== commentId) return c;
        const reactions: ReactionMap = { ...c.reactions };
        if (reactions[me.id] === emoji) {
          delete reactions[me.id];
        } else {
          reactions[me.id] = emoji;
        }
        return { ...c, reactions };
      }),
    }));
  },

  markContextResolved: (contextId) => {
    const now = Date.now();
    set((s) => ({
      contexts: s.contexts.map((c) =>
        c.id === contextId ? { ...c, status: 'resolved' as ContextStatus, updated_at: now } : c,
      ),
    }));
  },

  markContextActive: (contextId) => {
    const now = Date.now();
    set((s) => ({
      contexts: s.contexts.map((c) =>
        c.id === contextId ? { ...c, status: 'active' as ContextStatus, updated_at: now } : c,
      ),
    }));
  },

  archiveContext: (contextId) => {
    const now = Date.now();
    set((s) => ({
      contexts: s.contexts.map((c) =>
        c.id === contextId ? { ...c, status: 'archived' as ContextStatus, updated_at: now } : c,
      ),
    }));
  },

  generateDeepLink: (contextId) => {
    const now = Date.now();
    const token = `mock-${now.toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const dl: DeepLinkToken = {
      token,
      context_id: contextId,
      user_id: CURRENT_USER.id,
      created_at: now,
      expires_at: now + 5 * MIN,
      consumed_at: null,
    };
    set((s) => ({ deeplinks: [dl, ...s.deeplinks] }));
    return `eaide://collab/open?context=${encodeURIComponent(contextId)}&token=${token}&ts=${now}`;
  },

  consumeDeepLink: (token) => {
    const s = get();
    const dl = s.deeplinks.find((d) => d.token === token);
    if (!dl) return null;
    if (dl.consumed_at !== null) return null;
    if (Date.now() > dl.expires_at) return null;
    set((p) => ({
      deeplinks: p.deeplinks.map((d) =>
        d.token === token ? { ...d, consumed_at: Date.now() } : d,
      ),
    }));
    return dl.context_id;
  },
}));

// ---------- 选择器（派生）----------

/** 协作中心三个 Tab 的过滤结果 */
export const selectByTab = (tab: CollabCenterTab, userId: string = CURRENT_USER.id) => {
  const s = useCollabStore.getState();
  const sorted = [...s.contexts].sort((a, b) => b.updated_at - a.updated_at);
  if (tab === 'participated') {
    return sorted.filter((c) => isMyContext(c, userId));
  }
  if (tab === 'mentioned') {
    return sorted.filter((c) => isMentionedToMe(c, s.comments, userId));
  }
  // todo
  return sorted.filter((c) => isTodo(c, s.comments, userId));
};

/** @我的总未读数（用于 TopBar 红点） */
export const selectMentionCount = (userId: string = CURRENT_USER.id): number => {
  const s = useCollabStore.getState();
  return s.comments.filter(
    (c) => c.mentions.includes(userId) && c.author_id !== userId,
  ).length;
};

/** 某个 context 的主评论（parent_id === null），按时间正序 */
export const selectMainComments = (contextId: string): CollabComment[] => {
  const s = useCollabStore.getState();
  return s.comments
    .filter((c) => c.context_id === contextId && c.parent_id === null)
    .sort((a, b) => a.created_at - b.created_at);
};

/** 某条评论的 Thread 回复（最多 3 层递归 helper） */
export const selectThread = (parentId: string, maxDepth: number = 3): CollabComment[] => {
  const s = useCollabStore.getState();
  const out: CollabComment[] = [];
  const walk = (pid: string, depth: number): void => {
    if (depth >= maxDepth) return;
    const kids = getCommentChildren(s.comments, pid);
    kids.forEach((k) => {
      out.push(k);
      walk(k.id, depth + 1);
    });
  };
  walk(parentId, 0);
  return out;
};

// ---------- 导出时间工具 ----------

export { formatRelativeTime, withinEditWindow, MOCK_USERS };
