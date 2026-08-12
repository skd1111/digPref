/**
 * AuditApprovalCard —— 审批详情卡片（中心栏顶部）。
 *
 * 展示：
 *   - 任务 ID / 申请人 / 系统 / 环境
 *   - 风险等级 + 业务理由
 *   - 关联工单 + 资金影响
 *   - 合规检查状态
 */
import { useAuditStore, formatAmount, formatRelativeTime, RISK_COLORS, TYPE_LABELS } from '@/store/auditStore';

interface AuditApprovalCardProps {
  taskId: string;
}

export function AuditApprovalCard({ taskId }: AuditApprovalCardProps): JSX.Element {
  const task = useAuditStore((s) => s.tasks.find((t) => t.id === taskId));

  if (!task) {
    return (
      <div className="flex h-32 items-center justify-center text-2xs" style={{ color: '#616161' }}>
        任务不存在
      </div>
    );
  }

  const risk = RISK_COLORS[task.risk_level];

  return (
    <div
      className="audit-approval-card flex-shrink-0 border-b p-4"
      style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
    >
      {/* 头部：风险徽章 + 任务 ID + 时间 */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className="rounded px-2 py-0.5 text-2xs font-bold"
            style={{ backgroundColor: risk.bg, color: risk.fg }}
          >
            {risk.icon} {risk.label}
          </span>
          <span
            className="rounded px-2 py-0.5 text-2xs font-mono"
            style={{ backgroundColor: '#ffffff', color: '#0451a5', border: '1px solid #d4d4d4' }}
          >
            {TYPE_LABELS[task.task_type]}
          </span>
          <span className="font-mono text-2xs" style={{ color: '#616161' }}>
            {task.id}
          </span>
        </div>
        <div className="text-2xs" style={{ color: '#616161' }}>
          提交于 {formatRelativeTime(task.created_at)}
        </div>
      </div>

      {/* 业务理由 */}
      <h2 className="mb-3 text-ui-lg font-semibold" style={{ color: '#ffffff' }}>
        {task.business_reason}
      </h2>

      {/* 信息行 */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-ui">
        <InfoRow label="申请人" value={task.applicant_name} />
        <InfoRow
          label="目标系统"
          value={<span style={{ color: '#0451a5' }}>{task.target_system}</span>}
        />
        <InfoRow
          label="环境"
          value={
            <span
              className="rounded px-1.5 font-mono"
              style={{
                backgroundColor: task.environment === 'prod' ? '#fbeaea' : '#ffffff',
                color: task.environment === 'prod' ? '#cd3131' : '#333333',
                border: `1px solid ${task.environment === 'prod' ? '#cd3131' : '#1f1f1f'}`,
              }}
            >
              {task.environment}
            </span>
          }
        />
        <InfoRow
          label="资金影响"
          value={
            <span style={{ color: task.estimated_amount ? '#b25c1a' : '#616161' }}>
              {formatAmount(task.estimated_amount)}
            </span>
          }
        />
        <InfoRow
          label="审批 ID"
          value={<span className="font-mono text-2xs">{task.approval_id ?? '—'}</span>}
        />
        <InfoRow
          label="运行 ID"
          value={<span className="font-mono text-2xs">{task.run_id ?? '—'}</span>}
        />
      </div>

      {/* 关联工单 */}
      {task.related_tickets.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-2xs font-semibold" style={{ color: '#616161' }}>
            关联工单
          </div>
          <div className="flex flex-wrap gap-1.5">
            {task.related_tickets.map((t) => (
              <span
                key={t}
                className="rounded px-2 py-0.5 font-mono text-2xs"
                style={{ backgroundColor: '#ffffff', color: '#0451a5', border: '1px solid #d4d4d4' }}
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }): JSX.Element {
  return (
    <div className="flex items-center gap-2">
      <span className="text-2xs" style={{ color: '#616161' }}>
        {label}
      </span>
      <span className="text-2xs" style={{ color: '#1f1f1f' }}>
        {value}
      </span>
    </div>
  );
}
