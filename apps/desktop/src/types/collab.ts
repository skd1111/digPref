/**
 * collab.ts —— Phase 9 任务级协作引擎前端类型定义。
 *
 * 设计文档：[docs/design/phase-9-collab-engine.md](../docs/design/phase-9-collab-engine.md)
 * 实现文档：[docs/implementation/collab-engine.md](../docs/implementation/collab-engine.md)
 *
 * 范围（V0 MVP 前端 mock）：
 *   - 7 类锚点类型
 *   - Context / Comment / Reaction / Subscription / Notification / DeepLinkToken
 *   - 当前用户 + mock 同事列表
 *   - 协作中心 Tab 枚举
 *
 * 后续接入 Phase 8 Server 时，本文件作为前后端契约的 TS 镜像（与 Python `collab/models.py` 对齐）。
 */

// ---------- 锚点类型 ----------

export type AnchorType =
  | 'code_line'         // Monaco 行级评论
  | 'sql_block'         // SQL 变更讨论
  | 'deploy_task'       // 部署任务讨论
  | 'approval_ticket'   // 审批单讨论
  | 'log_segment'       // 日志片段讨论
  | 'hotswap_task'      // 热更任务讨论
  | 'custom';           // 自定义上下文

export interface AnchorPayload {
  /** code_line: {file, line, commit} */
  /** deploy_task: {deploy_id} */
  /** approval_ticket: {ticket_id} */
  /** log_segment: {file, start_line, end_line} */
  /** hotswap_task: {task_id} */
  /** custom: {type, payload: any} */
  [key: string]: unknown;
}

// ---------- Context（锚点）----------

export type ContextStatus = 'active' | 'resolved' | 'archived';

export interface CollabContext {
  id: string;                          // 格式：{anchor_type}:{source_id}
  anchor_type: AnchorType;
  anchor_payload: AnchorPayload;
  target_env?: 'prod' | 'uat' | 'dev' | 'test' | 'staging';
  related_ticket_id?: string;
  title: string;
  /** 简短描述，列表用 */
  summary: string;
  created_by: string;                  // user_id
  created_by_name: string;
  created_at: number;
  updated_at: number;
  status: ContextStatus;
  /** 参与者 user_id 列表（订阅者 + 显式 @） */
  participants: string[];
  participant_names: string[];
  /** 评论数（衍生字段，避免列表 join） */
  comment_count: number;
}

// ---------- Comment + Thread 嵌套 ----------

export type ReactionEmoji = '👍' | '👎' | '✅' | '❌' | '👀';

/** Reaction 字典：{user_id: emoji}——单选 toggle，不做"已读回执" */
export type ReactionMap = Record<string, ReactionEmoji>;

export interface CollabComment {
  id: string;
  context_id: string;
  /** 父评论 ID（Thread 回复）；null = 主评论 */
  parent_id: string | null;
  author_id: string;
  author_name: string;
  /** 明文（demo）；后续接入 Phase 8 时改为 AES-256-GCM 密文（`content_encrypted`） */
  content: string;
  /** SHA-256(content)；后续 Phase 8 真实计算 */
  content_hash: string;
  /** 解析出的被 @ user_id 列表 */
  mentions: string[];
  reactions: ReactionMap;
  is_edited: boolean;
  created_at: number;
  updated_at: number | null;
  /** 5 分钟撤回窗口 demo：UI 用 */
  can_withdraw: boolean;
  can_edit: boolean;
}

// ---------- Notification ----------

export type NotifyType = 'mention' | 'reply' | 'new_comment' | 'resolve' | 'share';

export interface CollabNotification {
  id: string;
  user_id: string;
  context_id: string;
  comment_id: string;
  notify_type: NotifyType;
  is_read: boolean;
  created_at: number;
}

// ---------- Deep Link ----------

export interface DeepLinkToken {
  token: string;
  context_id: string;
  user_id: string;
  created_at: number;
  expires_at: number;
  consumed_at: number | null;
}

// ---------- 当前用户 + Mock 协作者 ----------

export interface CollabUser {
  id: string;
  name: string;
  role: 'dev' | 'dba' | 'sre' | 'data' | 'reviewer' | 'admin';
  avatar_color: string;                // indigo / teal / orange / purple 等
}

/** 当前登录用户（MVP 固定为「张伟」——最常见业务操作者） */
export const CURRENT_USER: CollabUser = {
  id: 'u-zhangwei',
  name: '张伟',
  role: 'dev',
  avatar_color: '#6366f1',
};

/** 8 人 mock 协作者名单（覆盖 dev / dba / sre / data / reviewer / admin） */
export const MOCK_USERS: CollabUser[] = [
  { id: 'u-zhangwei',  name: '张伟',   role: 'dev',      avatar_color: '#6366f1' },
  { id: 'u-lina',      name: '李娜',   role: 'dba',      avatar_color: '#059669' },
  { id: 'u-mingyu',    name: '王明宇', role: 'sre',      avatar_color: '#795e26' },
  { id: 'u-chenjing',  name: '陈静',   role: 'data',     avatar_color: '#c586c0' },
  { id: 'u-wanglei',   name: '王磊',   role: 'dev',      avatar_color: '#cd3131' },
  { id: 'u-zhaomin',   name: '赵敏',   role: 'reviewer', avatar_color: '#0451a5' },
  { id: 'u-zhaolin',   name: '赵琳',   role: 'dev',      avatar_color: '#b25c1a' },
  { id: 'u-shen',      name: '沈雷',   role: 'admin',    avatar_color: '#b5cea8' },
];

/** user_id → CollabUser 快速查找 */
export const USER_BY_ID: Record<string, CollabUser> = Object.fromEntries(
  MOCK_USERS.map((u) => [u.id, u]),
);

// ---------- 协作中心 Tab 枚举 ----------

export type CollabCenterTab = 'participated' | 'mentioned' | 'todo';

// ---------- UI 视觉规范 ----------

/** 主色：indigo（与已有 4 mode 配色：green/orange/teal/purple 区分） */
export const COLLAB_ACCENT = '#6366f1';
export const COLLAB_ACCENT_DARK = '#4f46e5';
export const COLLAB_ACCENT_BG = 'rgba(99, 102, 241, 0.12)';

export const ANCHOR_LABELS: Record<AnchorType, { label: string; icon: string; color: string }> = {
  code_line:       { label: '代码行',     icon: '⌨', color: '#0451a5' },
  sql_block:       { label: 'SQL 块',     icon: '⊞', color: '#059669' },
  deploy_task:     { label: '部署任务',   icon: '🚀', color: '#795e26' },
  approval_ticket: { label: '审批单',     icon: '✓', color: '#c586c0' },
  log_segment:     { label: '日志片段',   icon: '⌘', color: '#b25c1a' },
  hotswap_task:    { label: 'JVM 热更',   icon: '⚡', color: '#cd3131' },
  custom:          { label: '自定义',     icon: '◉', color: '#616161' },
};

export const STATUS_LABELS: Record<ContextStatus, string> = {
  active: '进行中',
  resolved: '已解决',
  archived: '已归档',
};
