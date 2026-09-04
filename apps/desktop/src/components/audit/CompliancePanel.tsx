/**
 * CompliancePanel —— 合规检查结果（右栏底部）+ 资金影响。
 *
 * 展示：
 *   - 合规检查通过/警告状态
 *   - 合规条目列表（监管要求 / 内部规则 / 业务规则）
 *   - 资金影响（金额 + 等级提示）
 */
import { useAuditStore, formatAmount } from '@/store/auditStore';

interface CompliancePanelProps {
  taskId: string;
}

export function CompliancePanel({ taskId }: CompliancePanelProps): JSX.Element {
  const task = useAuditStore((s) => s.tasks.find((t) => t.id === taskId));

  if (!task) return <div />;

  const pass = task.compliance_passed;
  const amount = task.estimated_amount;
  const amountLevel =
    amount === null
      ? null
      : amount >= 1_000_000
        ? { color: '#cd3131', label: '高额' }
        : amount >= 100_000
          ? { color: '#795e26', label: '中等' }
          : { color: '#059669', label: '低额' };

  return (
    <div
      className="compliance-panel flex flex-col border-t"
      style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
    >
      {/* 合规检查 */}
      <div className="flex-shrink-0 px-3 py-2">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-ui font-semibold uppercase tracking-wider" style={{ color: '#333333' }}>
            🛡 合规检查
          </h3>
          <span
            className="rounded px-2 py-0.5 text-2xs font-bold"
            style={{
              backgroundColor: pass ? '#4ec9b022' : '#f4877122',
              color: pass ? '#059669' : '#cd3131',
              border: `1px solid ${pass ? '#059669' : '#cd3131'}`,
            }}
          >
            {pass ? '✓ 通过' : '⚠ 警告'}
          </span>
        </div>
        <ul className="space-y-1">
          {task.compliance_notes.map((note, i) => (
            <li
              key={i}
              className="text-2xs"
              style={{
                color: note.startsWith('⚠') ? '#cd3131' : '#1f1f1f',
                lineHeight: 1.5,
              }}
            >
              {note}
            </li>
          ))}
        </ul>
      </div>

      {/* 资金影响 */}
      {amount !== null && (
        <div
          className="flex-shrink-0 border-t px-3 py-2"
          style={{ borderColor: '#d4d4d4' }}
        >
          <h3 className="mb-2 text-ui font-semibold uppercase tracking-wider" style={{ color: '#333333' }}>
            💰 资金影响
          </h3>
          <div className="flex items-baseline gap-2">
            <span
              className="text-ui-lg font-bold"
              style={{ color: amountLevel?.color }}
            >
              {formatAmount(amount)}
            </span>
            {amountLevel && (
              <span
                className="rounded px-1.5 text-2xs"
                style={{ backgroundColor: `${amountLevel.color}22`, color: amountLevel.color }}
              >
                {amountLevel.label}
              </span>
            )}
          </div>
          <p className="mt-1 text-2xs" style={{ color: '#616161' }}>
            需财务联签 + 复审周期 ≥ 4h
          </p>
        </div>
      )}

      {/* Phase 联动提示 */}
      <div
        className="flex-shrink-0 border-t px-3 py-2"
        style={{ borderColor: '#d4d4d4' }}
      >
        <h3 className="mb-1 text-ui font-semibold uppercase tracking-wider" style={{ color: '#333333' }}>
          🔗 联动
        </h3>
        <div className="space-y-0.5 text-2xs" style={{ color: '#616161' }}>
          {task.task_type === 'hotswap' && (
            <div>⚙ Arthas 热更 - 字节码结构校验已通过</div>
          )}
          {task.task_type === 'sql' && task.environment === 'prod' && (
            <div>🗄 运营专家模式 - HITL 二次确认链路</div>
          )}
          <div>📚 知识库 - 制度 / 业务规则证据（接入中）</div>
          <div>📋 业务功能点 - 影响范围自动评估（接入中）</div>
        </div>
      </div>
    </div>
  );
}
