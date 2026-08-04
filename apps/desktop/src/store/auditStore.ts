/**
 * auditStore —— 审核专家工作台状态（Phase 5 MVP，前端 mock 数据）。
 *
 * 范围：
 *   - 审批任务列表（mock 5-10 条，涵盖不同 risk/level/status）
 *   - 当前选中的任务
 *   - 筛选/排序 UI 状态
 *
 * 后端（14.5 天排期）会替换 mock 数据来源；zustand 接口保持兼容即可平滑切换。
 */
import { create } from 'zustand';
import { ipc } from '@/ipc/invoke';

export type RiskLevel = 'high' | 'medium' | 'low';
export type TaskStatus = 'pending' | 'approved' | 'rejected' | 'questioned' | 'delegated' | 'expired';
export type TaskType = 'sql' | 'config' | 'code' | 'deploy' | 'hotswap';
export type MfaMethod = 'totp' | 'password' | 'windows_hello' | null;

export interface AuditTask {
  id: string;
  approval_id: string | null;       // 对应 LangGraph approval_id
  run_id: string | null;
  applicant_id: string;
  applicant_name: string;
  reviewer_id: string | null;
  task_type: TaskType;
  risk_level: RiskLevel;
  status: TaskStatus;
  target_system: string;
  environment: string;
  business_reason: string;
  /** Monaco Diff: { before, after, language } */
  diff: { before: string; after: string; language: string; summary: string };
  estimated_amount: number | null;   // 资金影响 (元)
  compliance_passed: boolean;
  compliance_notes: string[];
  related_tickets: string[];
  created_at: number;
  reviewed_at: number | null;
  review_comment: string | null;
}

export interface EvidenceEvent {
  id: string;
  task_id: string;
  actor: string;
  action: 'created' | 'risk_assessed' | 'comment' | 'mfa_pass' | 'mfa_fail' | 'approved' | 'rejected' | 'delegated' | 'questioned' | 'executed';
  detail: string;
  signature: string;   // 模拟哈希签名
  created_at: number;
}

interface AuditState {
  tasks: AuditTask[];
  evidence: EvidenceEvent[];
  selectedTaskId: string | null;
  filter: {
    status: TaskStatus | 'all';
    risk: RiskLevel | 'all';
    type: TaskType | 'all';
  };
  search: string;
  loading: boolean;

  // V1: TOTP + 双人复核
  currentTotp: string;                  // 当前 demo 用户的 TOTP（6 位）
  dualFirstApprover: string | null;      // 当前用户已记录第一审批人？
  publicKeyPem: string | null;            // 服务端 RSA 公钥（用于离线验签）

  // Actions
  selectTask: (id: string) => void;
  setFilter: (patch: Partial<AuditState['filter']>) => void;
  setSearch: (q: string) => void;
  /** 模拟审批操作（前端演练；接后端时换成 IPC） */
  approve: (taskId: string, comment: string, mfaVerified: boolean) => void;
  reject: (taskId: string, comment: string, mfaVerified: boolean) => void;
  question: (taskId: string, comment: string) => void;
  delegate: (taskId: string, toUser: string) => void;
  /** V1: 真实 IPC 审批（带 TOTP + RSA） */
  decideReal: (
    taskId: string,
    action: 'approve' | 'reject' | 'delegate' | 'inquire' | 'withdraw',
    reason: string,
    totpCode: string,
  ) => Promise<{ ok: boolean; new_status?: string; error?: string }>;
  /** V1: 双人复核第一审批 */
  dualFirstApproveReal: (
    taskId: string,
    reason: string,
    totpCode: string,
  ) => Promise<{ ok: boolean; error?: string }>;
  /** V1: 双人复核第二审批 */
  dualSecondApproveReal: (
    taskId: string,
    reason: string,
    totpCode: string,
  ) => Promise<{ ok: boolean; error?: string }>;
  /** V1: 验证签名链 */
  verifyChainReal: (taskId: string) => Promise<{ valid: boolean; action_count: number; rsa_signed_actions: number }>;
  /** V1: 获取当前 TOTP（demo） */
  refreshTotp: (username: string) => Promise<void>;
  /** V1: 获取 RSA 公钥 */
  refreshPublicKey: () => Promise<void>;
}

// ---------- 时间常量 ----------

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

export const useAuditStore = create<AuditState>((set) => ({
  tasks: [],
  evidence: [],
  selectedTaskId: null,
  filter: { status: 'all', risk: 'all', type: 'all' },
  search: '',
  loading: false,

  selectTask: (id) => set({ selectedTaskId: id }),

  setFilter: (patch) => set((s) => ({ filter: { ...s.filter, ...patch } })),

  setSearch: (q) => set({ search: q }),

  approve: (taskId, comment, mfaVerified) => {
    if (!mfaVerified) return;
    const now = Date.now();
    set((s) => ({
      tasks: s.tasks.map((t) =>
        t.id === taskId
          ? { ...t, status: 'approved', reviewed_at: now, review_comment: comment, reviewer_id: 'u-shen' }
          : t,
      ),
      evidence: [
        ...s.evidence,
        {
          id: `ev-${taskId}-${now}-mfa`,
          task_id: taskId,
          actor: '沈雷 (审核专家)',
          action: 'mfa_pass',
          detail: 'MFA 验证通过',
          signature: 'sha256:' + taskId.slice(0, 12) + 'k1l2',
          created_at: now - 1 * MIN,
        },
        {
          id: `ev-${taskId}-${now}-appr`,
          task_id: taskId,
          actor: '沈雷 (审核专家)',
          action: 'approved',
          detail: comment,
          signature: 'sha256:' + taskId.slice(0, 12) + 'm3n4',
          created_at: now,
        },
      ],
    }));
  },

  reject: (taskId, comment, mfaVerified) => {
    if (!mfaVerified) return;
    const now = Date.now();
    set((s) => ({
      tasks: s.tasks.map((t) =>
        t.id === taskId
          ? { ...t, status: 'rejected', reviewed_at: now, review_comment: comment, reviewer_id: 'u-shen' }
          : t,
      ),
      evidence: [
        ...s.evidence,
        {
          id: `ev-${taskId}-${now}-mfa`,
          task_id: taskId,
          actor: '沈雷 (审核专家)',
          action: 'mfa_pass',
          detail: 'MFA 验证通过',
          signature: 'sha256:' + taskId.slice(0, 12) + 'k1l2',
          created_at: now - 1 * MIN,
        },
        {
          id: `ev-${taskId}-${now}-rej`,
          task_id: taskId,
          actor: '沈雷 (审核专家)',
          action: 'rejected',
          detail: comment,
          signature: 'sha256:' + taskId.slice(0, 12) + 'o5p6',
          created_at: now,
        },
      ],
    }));
  },

  question: (taskId, comment) => {
    const now = Date.now();
    set((s) => ({
      tasks: s.tasks.map((t) =>
        t.id === taskId ? { ...t, status: 'questioned', review_comment: comment } : t,
      ),
      evidence: [
        ...s.evidence,
        {
          id: `ev-${taskId}-${now}-q`,
          task_id: taskId,
          actor: '沈雷 (审核专家)',
          action: 'questioned',
          detail: comment,
          signature: 'sha256:' + taskId.slice(0, 12) + 'q7r8',
          created_at: now,
        },
      ],
    }));
  },

  delegate: (taskId, toUser) => {
    const now = Date.now();
    set((s) => ({
      tasks: s.tasks.map((t) =>
        t.id === taskId ? { ...t, status: 'delegated', reviewer_id: toUser } : t,
      ),
      evidence: [
        ...s.evidence,
        {
          id: `ev-${taskId}-${now}-d`,
          task_id: taskId,
          actor: '沈雷 (审核专家)',
          action: 'delegated',
          detail: `委派给 ${toUser}`,
          signature: 'sha256:' + taskId.slice(0, 12) + 's9t0',
          created_at: now,
        },
      ],
    }));
  },

  // ====================== Phase 5 V1: 真实 IPC 审批（TOTP + RSA + 双人） ======================
  currentTotp: '',
  dualFirstApprover: null,
  publicKeyPem: null,

  decideReal: async (taskId, action, reason, totpCode) => {
    try {
      const resp = await ipc.auditDecide(taskId, {
        action_type: action,
        actor: 'u-shen',
        reason,
        mfa_verified: true,
        totp_code: totpCode,
        use_rsa: true,
      });
      const newStatus = resp.new_status;
      set((s) => ({
        tasks: s.tasks.map((t) =>
          t.id === taskId
            ? {
                ...t,
                status: newStatus as TaskStatus,
                reviewed_at: Date.now(),
                review_comment: reason,
                reviewer_id: 'u-shen',
              }
            : t,
        ),
        evidence: [
          ...s.evidence,
          {
            id: `ev-${taskId}-${Date.now()}-real`,
            task_id: taskId,
            actor: '沈雷 (审核专家)',
            action: newStatus === 'approved' ? 'approved' : newStatus === 'rejected' ? 'rejected' : 'mfa_pass',
            detail: `[V1 RSA + TOTP] ${reason}`,
            signature: 'rsa-pss:' + resp.signature_hash.slice(0, 16),
            created_at: Date.now(),
          },
        ],
      }));
      return { ok: true, new_status: newStatus };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, error: msg };
    }
  },

  dualFirstApproveReal: async (taskId, reason, totpCode) => {
    try {
      const resp = await ipc.auditDualFirst(taskId, {
        actor: 'u-shen',
        reason,
        mfa_verified: true,
        totp_code: totpCode,
        use_rsa: true,
      });
      set({ dualFirstApprover: resp.first_approver ?? null });
      set((s) => ({
        evidence: [
          ...s.evidence,
          {
            id: `ev-${taskId}-${Date.now()}-dual1`,
            task_id: taskId,
            actor: `${resp.first_approver} (第一审批)`,
            action: 'mfa_pass',
            detail: `[V1 RSA + TOTP 双人复核第一审批] ${reason}`,
            signature: 'rsa-pss:' + resp.signature_hash.slice(0, 16),
            created_at: Date.now(),
          },
        ],
      }));
      return { ok: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, error: msg };
    }
  },

  dualSecondApproveReal: async (taskId, reason, totpCode) => {
    try {
      const resp = await ipc.auditDualSecond(taskId, {
        actor: 'u-chenyu',  // 必须与 first_approver 不同
        reason,
        mfa_verified: true,
        totp_code: totpCode,
        use_rsa: true,
      });
      set((s) => ({
        tasks: s.tasks.map((t) =>
          t.id === taskId
            ? {
                ...t,
                status: 'approved' as TaskStatus,
                reviewed_at: Date.now(),
                review_comment: reason,
                reviewer_id: 'u-chenyu',
              }
            : t,
        ),
        evidence: [
          ...s.evidence,
          {
            id: `ev-${taskId}-${Date.now()}-dual2`,
            task_id: taskId,
            actor: `${resp.second_approver} (第二审批)`,
            action: 'approved',
            detail: `[V1 双人复核完成] ${reason}`,
            signature: 'rsa-pss:' + resp.signature_hash.slice(0, 16),
            created_at: Date.now(),
          },
        ],
      }));
      return { ok: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, error: msg };
    }
  },

  verifyChainReal: async (taskId) => {
    try {
      const resp = await ipc.auditVerifyChain(taskId);
      return {
        valid: resp.valid,
        action_count: resp.action_count,
        rsa_signed_actions: resp.rsa_signed_actions,
      };
    } catch (err) {
      return { valid: false, action_count: 0, rsa_signed_actions: 0 };
    }
  },

  refreshTotp: async (username: string) => {
    try {
      const resp = await ipc.auditGetTotp(username);
      set({ currentTotp: resp.totp_code });
    } catch {
      set({ currentTotp: '------' });  // 后端不可达占位
    }
  },

  refreshPublicKey: async () => {
    try {
      const resp = await ipc.auditGetPublicKey();
      set({ publicKeyPem: resp.public_key_pem });
    } catch {
      // ignore
    }
  },
}));

// ---------- 工具函数 ----------

export function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 1 * MIN) return '刚刚';
  if (diff < 1 * HOUR) return `${Math.floor(diff / MIN)} 分钟前`;
  if (diff < 1 * DAY) return `${Math.floor(diff / HOUR)} 小时前`;
  return `${Math.floor(diff / DAY)} 天前`;
}

export function formatAmount(amount: number | null): string {
  if (amount === null) return '—';
  if (amount >= 10_000) {
    return `¥${(amount / 10_000).toFixed(2)} 万`;
  }
  return `¥${amount.toLocaleString('zh-CN')}`;
}

export const RISK_COLORS: Record<RiskLevel, { fg: string; bg: string; icon: string; label: string }> = {
  high:   { fg: '#0e0e0e', bg: '#cd3131', icon: '🔴', label: '高风险' },
  medium: { fg: '#0e0e0e', bg: '#795e26', icon: '🟡', label: '中风险' },
  low:    { fg: '#0e0e0e', bg: '#059669', icon: '🟢', label: '低风险' },
};

export const STATUS_LABELS: Record<TaskStatus, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
  questioned: '问询中',
  delegated: '已委派',
  expired: '已过期',
};

export const TYPE_LABELS: Record<TaskType, string> = {
  sql: 'SQL',
  config: '配置',
  code: '代码',
  deploy: '部署',
  hotswap: 'JVM 热更',
};
