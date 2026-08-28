/**
 * ApprovalCard — HITL write-operation approval gate.
 *
 * Shows the agent's proposed operation (tool name + risk level) and offers
 * Approve / Reject buttons. Critical operations (DROP, TRUNCATE, grant/revoke)
 * require a second explicit confirmation.
 *
 * 卡片不展示原始调用参数（2026-08-25 用户反馈：args JSON 铺屏无决策价值）——
 * 参数详情已在思维链工具调用条目与后端审计（HITL_APPROVAL / AUTO_MODE_DECISION）
 * 全程留痕，卡片只留操作概要。
 *
 * UX:
 *   - Risk level drives border + accent color
 *   - Live countdown shows time until upstream timeout
 *   - Critical ops show a typed-confirmation field
 *   - Decision POSTs back through the Rust sse_bridge
 */
import { useEffect, useState } from 'react';
import type { ApprovalDecision, ApprovalRequest, ToolRiskLevel } from '@eaide/shared-protocol';
import { invoke } from '@/ipc/invoke';
import { useChatStore } from '@/store/chatStore';

interface Props {
  approval: ApprovalRequest;
  onDecided?: (decision: ApprovalDecision) => void;
}

/** 决策提交成功后的消息文案（卡片同步收起，不再永久卡「提交中」，2026-08-25） */
const DECISION_SUMMARY: Record<ApprovalDecision, string> = {
  approve: '✅ 已批准，正在继续执行…',
  approve_always: '✅ 已批准。此后本会话同类操作将自动执行（全程审计留痕）',
  reject: '🛑 已拒绝该操作。',
};

// Visual treatment per risk level
const RISK_THEME: Record<ToolRiskLevel, {
  border: string;
  accent: string;
  label: string;
  description: string;
  doubleConfirm: boolean;
}> = {
  read:     { border: 'border-border', accent: 'text-accent', label: 'Read',
              description: '只读操作', doubleConfirm: false },
  low:      { border: 'border-yellow-500/40', accent: 'text-yellow-300', label: 'Low',
              description: '单系统写，影响范围有限', doubleConfirm: false },
  medium:   { border: 'border-accent-warn', accent: 'text-accent-warn', label: 'Medium',
              description: '涉及多行写入', doubleConfirm: false },
  high:     { border: 'border-accent-danger/60', accent: 'text-accent-danger', label: 'High',
              description: 'DELETE / DDL / 跨系统', doubleConfirm: true },
  critical: { border: 'border-accent-danger', accent: 'text-accent-danger', label: 'Critical',
              description: 'DROP / TRUNCATE / GRANT / 系统表', doubleConfirm: true },
};

const DEFAULT_TIMEOUT_SEC = 1800;   // matches services/agent config

export function ApprovalCard({ approval, onDecided }: Props): JSX.Element {
  const theme = RISK_THEME[approval.riskLevel] ?? RISK_THEME.medium;
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState<string | null>(null);

  // ---- Countdown timer ----
  const [secondsLeft, setSecondsLeft] = useState(() => {
    const created = new Date(approval.createdAt).getTime();
    const elapsed = Math.max(0, (Date.now() - created) / 1000);
    return Math.max(0, Math.floor(DEFAULT_TIMEOUT_SEC - elapsed));
  });
  useEffect(() => {
    if (secondsLeft <= 0) return;
    const t = setInterval(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [secondsLeft]);

  // ---- Submit decision ----
  const submit = async (decision: ApprovalDecision): Promise<void> => {
    if (busy) return;
    if (theme.doubleConfirm && decision === 'approve' && confirmation.trim() !== approval.id) {
      setError(`请键入审批编号 ${approval.id} 以确认`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // 使用 Tauri 获取真实操作系统用户名（而非浏览器 UA 字符串）
      let operator = 'unknown';
      try {
        const { invoke: tauriInvoke } = await import('@tauri-apps/api/core');
        operator = (await tauriInvoke('credential_service_name')) as string;
      } catch {
        // credential_service_name 返回服务名，这里用 hostname 作 fallback
        operator = window.location.hostname || 'desktop-user';
      }
      await invoke('agent_approval', {
        approvalId: approval.id,
        decision,
        operator,
      });
      // 提交成功 → 卡片改写为结果文案并剥离审批区（否则 busy 永久卡「提交中」）
      useChatStore.getState().resolvePendingApproval(approval.id, DECISION_SUMMARY[decision]);
      onDecided?.(decision);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  };

  const minutes = Math.floor(secondsLeft / 60);
  const seconds = secondsLeft % 60;
  const countdownColor = secondsLeft < 60
    ? 'text-accent-danger'
    : secondsLeft < 300
      ? 'text-accent-warn'
      : 'text-fg-muted';

  return (
    <div className={`my-3 rounded border-2 ${theme.border} bg-bg-subtle p-4 shadow-lg`}>
      {/* ---- Header ---- */}
      <header className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wide ${theme.accent} bg-bg-code`}>
            ⚠ {theme.label}
          </span>
          <span className="text-sm text-fg-muted">{theme.description}</span>
        </div>
        <span className={`font-mono text-xs ${countdownColor}`}>
          ⏱ {String(minutes).padStart(2, '0')}:{String(seconds).padStart(2, '0')}
        </span>
      </header>

      {/* ---- 操作概要：不展示原始 args（参数在思维链/审计留痕） ---- */}
      <div className="mb-3 rounded bg-bg-code p-2 text-xs">
        <div className="mb-1 font-semibold text-fg-muted">操作：</div>
        <div className="text-fg">
          <code className="font-mono">{approval.plan.server} · {approval.plan.name}</code>
          <span className="ml-2 text-fg-muted">执行参数详见右侧执行过程与审计记录，此处不展示</span>
        </div>
      </div>

      {/* ---- Phase 18：推荐选项（为空时保持二元审批，向后兼容） ---- */}
      {approval.options && approval.options.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-xs font-semibold text-fg-muted">候选方案（智能体推荐项已高亮）：</div>
          <ul className="space-y-1">
            {approval.options.map((o) => {
              const isRecommended = o.id === approval.recommendedOptionId;
              return (
                <li
                  key={o.id}
                  className="rounded border px-2 py-1.5 text-xs"
                  style={{
                    borderColor: isRecommended ? '#059669' : '#d1d5db',
                    backgroundColor: isRecommended ? 'rgba(5,150,105,0.08)' : 'transparent',
                  }}
                >
                  <span className="font-medium" style={{ color: '#1f1f1f' }}>
                    {isRecommended ? '⭐ ' : ''}{o.label}
                  </span>
                  {o.adjustedPlan && (
                    <span className="ml-2" style={{ color: '#616161' }}>{o.adjustedPlan}</span>
                  )}
                  {o.riskNote && (
                    <span className="ml-2" style={{ color: '#b45309' }}>⚠ {o.riskNote}</span>
                  )}
                </li>
              );
            })}
          </ul>
          {approval.recommendationReason && (
            <div className="mt-1 text-2xs" style={{ color: '#616161' }}>
              推荐理由：{approval.recommendationReason}
            </div>
          )}
        </div>
      )}

      {/* ---- Double-confirm for high/critical ---- */}
      {theme.doubleConfirm && (
        <div className="mb-3">
          <label className="mb-1 block text-xs text-fg-muted">
            ⚠ <strong>高危操作</strong>：请键入审批编号
            <code className="mx-1 rounded bg-bg-code px-1 py-0.5">{approval.id}</code>
            以确认执行
          </label>
          <input
            type="text"
            value={confirmation}
            onChange={(e) => setConfirmation(e.target.value)}
            placeholder={approval.id}
            className="w-full rounded border border-border bg-bg-code px-2 py-1 font-mono text-xs focus:border-accent-danger focus:outline-none"
            disabled={busy}
          />
        </div>
      )}

      {/* ---- Error banner ---- */}
      {error && (
        <div className="mb-3 rounded border border-accent-danger bg-bg-code p-2 text-xs text-accent-danger">
          {error}
        </div>
      )}

      {/* ---- Actions ---- */}
      <div className="flex gap-2">
        <button
          onClick={() => submit('approve')}
          disabled={busy}
          className="flex-1 rounded bg-accent-approval px-3 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
        >
          {busy ? '提交中…' : 'Approve'}
        </button>
        {/* 同类免审批（2026-08-25）：仅非双重确认的风险级提供；
            high/critical 必须逐次人工确认，不提供长期豁免 */}
        {!theme.doubleConfirm && (
          <button
            onClick={() => submit('approve_always')}
            disabled={busy}
            title="批准，且本会话内同一工具（同服务·同名）的后续操作自动放行；DROP/TRUNCATE 等硬阻断不受影响"
            className="flex-1 rounded border border-accent-approval bg-transparent px-3 py-2 text-sm font-semibold text-accent-approval hover:opacity-80 disabled:opacity-50"
          >
            {busy ? '提交中…' : '此后都按此执行'}
          </button>
        )}
        <button
          onClick={() => submit('reject')}
          disabled={busy}
          className="flex-1 rounded bg-bg-code px-3 py-2 text-sm font-semibold text-fg-muted hover:bg-accent-danger hover:text-white disabled:opacity-50"
        >
          Reject
        </button>
      </div>

      {/* ---- Timeout hint ---- */}
      {secondsLeft === 0 && (
        <div className="mt-2 text-center text-xs text-accent-danger">
          已超时 — Agent 将自动取消
        </div>
      )}
    </div>
  );
}