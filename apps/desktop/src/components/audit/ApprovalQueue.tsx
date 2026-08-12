/**
 * ApprovalQueue —— 审核专家工作台左栏：审批任务队列。
 *
 * 功能：
 *   - 任务列表（按 risk + 时间倒序）
 *   - 顶部筛选条（status / risk / type）+ 搜索框
 *   - 每行：风险色条 + 类型 + 标题（业务理由）+ 申请人 + 时间 + 环境
 *   - 单击切换右侧详情（通过 auditStore.selectTask）
 */
import { useMemo } from 'react';
import {
  useAuditStore,
  formatRelativeTime,
  RISK_COLORS,
  STATUS_LABELS,
  TYPE_LABELS,
  type RiskLevel,
  type TaskStatus,
  type TaskType,
} from '@/store/auditStore';

export function ApprovalQueue(): JSX.Element {
  const tasks = useAuditStore((s) => s.tasks);
  const selectedTaskId = useAuditStore((s) => s.selectedTaskId);
  const selectTask = useAuditStore((s) => s.selectTask);
  const filter = useAuditStore((s) => s.filter);
  const setFilter = useAuditStore((s) => s.setFilter);
  const search = useAuditStore((s) => s.search);
  const setSearch = useAuditStore((s) => s.setSearch);

  // 过滤 + 排序
  const filtered = useMemo(() => {
    return tasks
      .filter((t) => filter.status === 'all' || t.status === filter.status)
      .filter((t) => filter.risk === 'all' || t.risk_level === filter.risk)
      .filter((t) => filter.type === 'all' || t.task_type === filter.type)
      .filter((t) => {
        if (!search) return true;
        const q = search.toLowerCase();
        return (
          t.business_reason.toLowerCase().includes(q) ||
          t.applicant_name.toLowerCase().includes(q) ||
          t.target_system.toLowerCase().includes(q) ||
          t.id.toLowerCase().includes(q)
        );
      })
      .sort((a, b) => {
        // 风险等级降序（high > medium > low）
        const order: Record<RiskLevel, number> = { high: 0, medium: 1, low: 2 };
        if (order[a.risk_level] !== order[b.risk_level]) {
          return order[a.risk_level] - order[b.risk_level];
        }
        // 同风险按时间倒序
        return b.created_at - a.created_at;
      });
  }, [tasks, filter, search]);

  // 统计
  const stats = useMemo(() => {
    const pending = tasks.filter((t) => t.status === 'pending').length;
    const highRiskPending = tasks.filter(
      (t) => t.status === 'pending' && t.risk_level === 'high',
    ).length;
    const approvedToday = tasks.filter(
      (t) => t.status === 'approved' && t.reviewed_at && Date.now() - t.reviewed_at < 1 * 24 * 60 * 60_000,
    ).length;
    return { pending, highRiskPending, approvedToday };
  }, [tasks]);

  return (
    <div className="approval-queue flex h-full flex-col" style={{ backgroundColor: '#f3f3f3' }}>
      {/* 顶部统计 */}
      <div
        className="flex-shrink-0 border-b px-3 py-2"
        style={{ borderColor: '#d4d4d4' }}
      >
        <div className="mb-2 flex items-center justify-between">
          <h3
            className="text-ui font-semibold uppercase tracking-wider"
            style={{ color: '#333333' }}
          >
            审批工作台
          </h3>
          <span
            className="rounded-full px-2 py-0.5 text-2xs font-bold"
            style={{ backgroundColor: stats.highRiskPending > 0 ? '#cd3131' : '#ececec', color: '#ffffff' }}
          >
            {stats.pending} 待审
          </span>
        </div>
        <div className="flex gap-3 text-2xs" style={{ color: '#616161' }}>
          <span>今日待审 <span style={{ color: '#b25c1a' }}>{stats.pending}</span></span>
          <span>已批 <span style={{ color: '#059669' }}>{stats.approvedToday}</span></span>
          {stats.highRiskPending > 0 && (
            <span className="font-bold" style={{ color: '#cd3131' }}>
              🔴 {stats.highRiskPending} 高危
            </span>
          )}
        </div>
      </div>

      {/* 搜索 + 筛选 */}
      <div
        className="flex-shrink-0 space-y-2 border-b px-3 py-2"
        style={{ borderColor: '#d4d4d4' }}
      >
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索业务理由 / 申请人 / 系统..."
          className="w-full rounded px-2 py-1 text-2xs outline-none"
          style={{ backgroundColor: '#ffffff', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
        />
        <div className="flex gap-1">
          <select
            value={filter.status}
            onChange={(e) => setFilter({ status: e.target.value as TaskStatus | 'all' })}
            className="flex-1 rounded px-1.5 py-0.5 text-2xs outline-none"
            style={{ backgroundColor: '#ffffff', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
          >
            <option value="all">全部状态</option>
            <option value="pending">待审批</option>
            <option value="questioned">问询中</option>
            <option value="approved">已批准</option>
            <option value="rejected">已驳回</option>
            <option value="delegated">已委派</option>
          </select>
          <select
            value={filter.risk}
            onChange={(e) => setFilter({ risk: e.target.value as RiskLevel | 'all' })}
            className="flex-1 rounded px-1.5 py-0.5 text-2xs outline-none"
            style={{ backgroundColor: '#ffffff', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
          >
            <option value="all">全部风险</option>
            <option value="high">🔴 高</option>
            <option value="medium">🟡 中</option>
            <option value="low">🟢 低</option>
          </select>
        </div>
        <div className="flex gap-1">
          {(['all', 'sql', 'config', 'code', 'deploy', 'hotswap'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setFilter({ type: t as TaskType | 'all' })}
              className="flex-1 rounded px-1.5 py-0.5 text-2xs font-semibold transition-colors"
              style={{
                backgroundColor: filter.type === t ? '#007acc' : '#ffffff',
                color: filter.type === t ? '#ffffff' : '#616161',
                border: '1px solid #d4d4d4',
              }}
            >
              {t === 'all' ? '全部' : TYPE_LABELS[t as TaskType]}
            </button>
          ))}
        </div>
      </div>

      {/* 任务列表 */}
      <div className="flex-1 overflow-auto">
        {filtered.length === 0 ? (
          <div className="flex h-full items-center justify-center p-4 text-2xs" style={{ color: '#616161' }}>
            没有匹配的审批任务
          </div>
        ) : (
          filtered.map((t) => {
            const risk = RISK_COLORS[t.risk_level];
            const isSelected = selectedTaskId === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => selectTask(t.id)}
                className="w-full border-b px-3 py-2 text-left transition-colors"
                style={{
                  borderColor: '#d4d4d4',
                  backgroundColor: isSelected ? '#0e639c' : 'transparent',
                  borderLeft: isSelected ? '3px solid #007acc' : '3px solid transparent',
                }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span
                      style={{
                        backgroundColor: risk.bg,
                        color: risk.fg,
                        fontSize: 10,
                        padding: '1px 5px',
                        borderRadius: 3,
                        fontWeight: 700,
                      }}
                    >
                      {risk.label}
                    </span>
                    <span
                      className="font-mono text-2xs"
                      style={{ color: '#0451a5' }}
                    >
                      {TYPE_LABELS[t.task_type]}
                    </span>
                  </div>
                  <span className="text-2xs" style={{ color: '#616161' }}>
                    {formatRelativeTime(t.created_at)}
                  </span>
                </div>
                <p
                  className="mt-1 line-clamp-2 text-ui"
                  style={{ color: isSelected ? '#ffffff' : '#1f1f1f' }}
                >
                  {t.business_reason}
                </p>
                <div className="mt-1 flex items-center justify-between text-2xs" style={{ color: '#616161' }}>
                  <span>
                    {t.applicant_name} · {t.environment}
                  </span>
                  <span
                    className="rounded px-1.5"
                    style={{
                      backgroundColor: t.status === 'pending' ? '#ececec' : 'transparent',
                      color: t.status === 'pending' ? '#333333' : '#059669',
                    }}
                  >
                    {STATUS_LABELS[t.status]}
                  </span>
                </div>
              </button>
            );
          })
        )}
      </div>

      {/* 底部：批量操作提示（Phase 5 后续开发） */}
      <div
        className="flex-shrink-0 border-t px-3 py-1.5 text-2xs"
        style={{ borderColor: '#d4d4d4', color: '#616161' }}
      >
        ⚠ 批量审批仅允许低风险任务（后续开发）
      </div>
    </div>
  );
}
