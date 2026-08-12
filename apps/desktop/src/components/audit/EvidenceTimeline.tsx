/**
 * EvidenceTimeline —— 证据链时间线（右栏顶部）。
 *
 * 展示：
 *   - 申请 → 风险评估 → 合规扫描 → 审批 → 执行 完整链路
 *   - 每步显示：操作人、动作类型、详情、哈希签名、时间
 *   - 视觉形态：左侧时间轴 + 右侧详情
 */
import { useAuditStore, formatRelativeTime, type EvidenceEvent } from '@/store/auditStore';

interface EvidenceTimelineProps {
  taskId: string;
}

const ACTION_ICONS: Record<EvidenceEvent['action'], string> = {
  created: '📝',
  risk_assessed: '⚖️',
  comment: '💬',
  mfa_pass: '🔐',
  mfa_fail: '🔓',
  approved: '✅',
  rejected: '❌',
  delegated: '👤',
  questioned: '❓',
  executed: '🚀',
};

const ACTION_COLORS: Record<EvidenceEvent['action'], string> = {
  created: '#0451a5',
  risk_assessed: '#795e26',
  comment: '#616161',
  mfa_pass: '#059669',
  mfa_fail: '#cd3131',
  approved: '#059669',
  rejected: '#cd3131',
  delegated: '#c586c0',
  questioned: '#795e26',
  executed: '#007acc',
};

const ACTION_LABELS: Record<EvidenceEvent['action'], string> = {
  created: '提交申请',
  risk_assessed: '风险评估',
  comment: '批注/合规',
  mfa_pass: 'MFA 通过',
  mfa_fail: 'MFA 失败',
  approved: '已批准',
  rejected: '已驳回',
  delegated: '已委派',
  questioned: '问询中',
  executed: '已执行',
};

export function EvidenceTimeline({ taskId }: EvidenceTimelineProps): JSX.Element {
  const evidence = useAuditStore((s) => s.evidence.filter((e) => e.task_id === taskId));

  return (
    <div
      className="evidence-timeline flex h-full flex-col"
      style={{ backgroundColor: '#f3f3f3' }}
    >
      {/* 标题 */}
      <div
        className="flex-shrink-0 border-b px-3 py-2"
        style={{ borderColor: '#d4d4d4' }}
      >
        <h3 className="text-ui font-semibold uppercase tracking-wider" style={{ color: '#333333' }}>
          🔗 Evidence Chain
        </h3>
        <p className="mt-0.5 text-2xs" style={{ color: '#616161' }}>
          哈希签名链防篡改 · {evidence.length} 步
        </p>
      </div>

      {/* 时间线 */}
      <div className="flex-1 overflow-auto p-3">
        {evidence.length === 0 ? (
          <div className="text-2xs" style={{ color: '#616161' }}>
            暂无证据链
          </div>
        ) : (
          <div className="relative">
            {/* 左侧时间轴竖线 */}
            <div
              className="absolute left-3 top-0 bottom-0 w-px"
              style={{ backgroundColor: '#ececec' }}
            />
            {evidence.map((e) => {
              const color = ACTION_COLORS[e.action];
              return (
                <div key={e.id} className="relative mb-4 pl-9">
                  {/* 时间轴圆点 */}
                  <div
                    className="absolute left-0 top-0 flex h-6 w-6 items-center justify-center rounded-full text-2xs"
                    style={{
                      backgroundColor: color,
                      color: '#0e0e0e',
                      boxShadow: `0 0 0 2px #d0d0d0`,
                    }}
                  >
                    {ACTION_ICONS[e.action]}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span
                        className="text-2xs font-bold"
                        style={{ color }}
                      >
                        {ACTION_LABELS[e.action]}
                      </span>
                      <span className="text-2xs" style={{ color: '#616161' }}>
                        {formatRelativeTime(e.created_at)}
                      </span>
                    </div>
                    <div className="mt-0.5 text-2xs" style={{ color: '#333333' }}>
                      {e.actor}
                    </div>
                    <div className="mt-1 text-ui" style={{ color: '#1f1f1f' }}>
                      {e.detail}
                    </div>
                    <div className="mt-1 truncate font-mono text-2xs" style={{ color: '#059669' }} title={e.signature}>
                      {e.signature}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
