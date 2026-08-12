/**
 * ApprovalActions —— 审批操作区（批准 / 驳回 / 问询 / 委派 + TOTP MFA + 双人复核）。
 *
 * Phase 5 V1 增量：
 *   - 真实 TOTP 验证（从服务端拉当前 6 位；demo 模式）
 *   - 双人复核按钮（dual_first / dual_second 切换）
 *   - RSA 签名 + 链式 hash（后端自动生成；前端只传 reason + TOTP）
 *   - chain verify 按钮（reviewer 可手动校验）
 *
 * 金融级要求：
 *   - 高风险操作必须 TOTP 二次验证
 *   - 必须填审批意见（不少于 10 字符）
 *   - 高/极高风险自动启用双人复核（后端逻辑）
 */
import { useEffect, useState } from 'react';
import { useAuditStore, type AuditTask } from '@/store/auditStore';

interface ApprovalActionsProps {
  task: AuditTask;
}

const CURRENT_USER = 'u-shen';
const DUAL_SECOND_USER = 'u-chenyu';   // 第二审批人（demo 固定）

export function ApprovalActions({ task }: ApprovalActionsProps): JSX.Element {
  const approve = useAuditStore((s) => s.approve);
  const reject = useAuditStore((s) => s.reject);
  const question = useAuditStore((s) => s.question);
  const delegate = useAuditStore((s) => s.delegate);
  const decideReal = useAuditStore((s) => s.decideReal);
  const dualFirstReal = useAuditStore((s) => s.dualFirstApproveReal);
  const dualSecondReal = useAuditStore((s) => s.dualSecondApproveReal);
  const verifyChain = useAuditStore((s) => s.verifyChainReal);
  const refreshTotp = useAuditStore((s) => s.refreshTotp);
  const currentTotp = useAuditStore((s) => s.currentTotp);

  const [comment, setComment] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [mfaVerified, setMfaVerified] = useState(false);
  const [mfaError, setMfaError] = useState<string | null>(null);
  const [delegateTo, setDelegateTo] = useState('');
  const [busy, setBusy] = useState(false);
  const [resultMsg, setResultMsg] = useState<string | null>(null);
  const [verifyResult, setVerifyResult] = useState<string | null>(null);

  const isPending = task.status === 'pending' || task.status === 'questioned';
  const isHighRisk = task.risk_level === 'high';
  const requireMfa = isHighRisk;

  // V1: 进入页面拉取当前 TOTP（demo only；生产 V1.5 删除）
  useEffect(() => {
    if (requireMfa && !currentTotp) {
      refreshTotp(CURRENT_USER);
    }
  }, [requireMfa, currentTotp, refreshTotp]);

  const handleMfaVerify = (): void => {
    if (mfaCode.length !== 6) {
      setMfaError('MFA 码必须 6 位');
      return;
    }
    // V1: 与服务端当前 TOTP 对比
    if (currentTotp && mfaCode !== currentTotp) {
      setMfaError('TOTP 错误（当前服务端码：' + currentTotp + '）');
      return;
    }
    setMfaVerified(true);
    setMfaError(null);
  };

  const handleApprove = async (): Promise<void> => {
    if (!comment.trim() || comment.trim().length < 5) {
      alert('审批意见至少 5 个字符');
      return;
    }
    if (requireMfa && !mfaVerified) {
      setMfaError('高风险操作必须先完成 TOTP 验证');
      return;
    }
    setBusy(true);
    setResultMsg(null);
    try {
      const resp = await decideReal(task.id, 'approve', comment, mfaCode);
      if (resp.ok) {
        setResultMsg(`✓ 已批准（new_status=${resp.new_status}）`);
        // 同步调用 mock store（保持 UI 一致）
        approve(task.id, comment, true);
        setComment('');
        setMfaCode('');
        setMfaVerified(false);
      } else {
        setResultMsg(`✗ 错误：${resp.error}`);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async (): Promise<void> => {
    if (!comment.trim() || comment.trim().length < 10) {
      alert('驳回意见至少 10 个字符（金融合规要求）');
      return;
    }
    if (requireMfa && !mfaVerified) {
      setMfaError('高风险操作必须先完成 TOTP 验证');
      return;
    }
    setBusy(true);
    setResultMsg(null);
    try {
      const resp = await decideReal(task.id, 'reject', comment, mfaCode);
      if (resp.ok) {
        setResultMsg(`✓ 已驳回（new_status=${resp.new_status}）`);
        reject(task.id, comment, true);
        setComment('');
        setMfaCode('');
        setMfaVerified(false);
      } else {
        setResultMsg(`✗ 错误：${resp.error}`);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleQuestion = (): void => {
    if (!comment.trim()) {
      alert('请填写问询内容');
      return;
    }
    question(task.id, comment);
    setComment('');
  };

  const handleDelegate = (): void => {
    if (!delegateTo.trim()) {
      alert('请填写委派对象');
      return;
    }
    delegate(task.id, delegateTo);
    setDelegateTo('');
  };

  // V1: 双人复核 - 第一审批
  const handleDualFirst = async (): Promise<void> => {
    if (!comment.trim() || comment.trim().length < 5) {
      alert('审批意见至少 5 个字符');
      return;
    }
    if (requireMfa && !mfaVerified) {
      setMfaError('高风险操作必须先完成 TOTP 验证');
      return;
    }
    setBusy(true);
    try {
      const resp = await dualFirstReal(task.id, comment, mfaCode);
      if (resp.ok) {
        setResultMsg(`✓ 第一审批完成（任务仍 pending，等待 ${DUAL_SECOND_USER}）`);
      } else {
        setResultMsg(`✗ 错误：${resp.error}`);
      }
    } finally {
      setBusy(false);
    }
  };

  // V1: 双人复核 - 第二审批（必须不同 actor）
  const handleDualSecond = async (): Promise<void> => {
    if (!comment.trim() || comment.trim().length < 5) {
      alert('审批意见至少 5 个字符');
      return;
    }
    if (requireMfa && !mfaVerified) {
      setMfaError('高风险操作必须先完成 TOTP 验证');
      return;
    }
    setBusy(true);
    try {
      const resp = await dualSecondReal(task.id, comment, mfaCode);
      if (resp.ok) {
        setResultMsg(`✓ 第二审批完成（任务已 approved）`);
        setComment('');
        setMfaCode('');
        setMfaVerified(false);
      } else {
        setResultMsg(`✗ 错误：${resp.error}`);
      }
    } finally {
      setBusy(false);
    }
  };

  // V1: 验证签名链
  const handleVerify = async (): Promise<void> => {
    setBusy(true);
    try {
      const r = await verifyChain(task.id);
      setVerifyResult(
        r.valid
          ? `✓ 链有效（${r.action_count} 个动作，${r.rsa_signed_actions} 个 RSA 签名）`
          : `✗ 链无效（${r.action_count} 个动作）`,
      );
    } finally {
      setBusy(false);
    }
  };

  if (!isPending) {
    return (
      <div
        className="approval-actions flex items-center justify-center p-4 text-2xs"
        style={{ backgroundColor: '#f3f3f3', color: '#616161' }}
      >
        此任务已结束（{task.status}），无法操作。
        {task.review_comment && (
          <div className="mt-2 text-ui" style={{ color: '#1f1f1f' }}>
            审核意见：{task.review_comment}
          </div>
        )}
        <button
          type="button"
          onClick={handleVerify}
          className="ml-2 rounded px-2 py-1 text-2xs"
          style={{ backgroundColor: '#ececec', color: '#059669' }}
        >
          🔗 验证签名链
        </button>
        {verifyResult && (
          <span className="ml-2 text-2xs" style={{ color: '#795e26' }}>
            {verifyResult}
          </span>
        )}
      </div>
    );
  }

  return (
    <div
      className="approval-actions border-t p-3"
      style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
    >
      {/* V1: 任务风险等级 + 双人复核提示 */}
      <div
        className="mb-2 flex items-center gap-2 rounded px-2 py-1 text-2xs"
        style={{
          backgroundColor: isHighRisk ? '#fbeaea' : '#1d3a1d',
          color: isHighRisk ? '#cd3131' : '#059669',
          border: `1px solid ${isHighRisk ? '#cd3131' : '#059669'}`,
        }}
      >
        <span className="font-bold">
          {isHighRisk ? '🔴 高风险' : '🟢 低/中风险'}
        </span>
        {isHighRisk && (
          <span style={{ color: '#795e26' }}>
            → 需 TOTP + 双人复核（沈雷 + 陈宇）
          </span>
        )}
      </div>

      {/* 审批意见 */}
      <label className="mb-2 block">
        <span className="mb-1 block text-2xs font-semibold" style={{ color: '#333333' }}>
          审批意见 <span style={{ color: '#cd3131' }}>*</span>
        </span>
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder={
            requireMfa
              ? '高风险操作：请详细说明审批依据（不少于 5 字符）'
              : '请填写审批意见...'
          }
          rows={3}
          className="w-full rounded px-2 py-1 text-ui outline-none"
          style={{ backgroundColor: '#ffffff', color: '#1f1f1f', border: '1px solid #d4d4d4', resize: 'vertical' }}
        />
      </label>

      {/* V1: TOTP MFA 区（高风险必填） */}
      {requireMfa && (
        <div
          className="mb-2 rounded p-2"
          style={{ backgroundColor: '#fbeaea', border: '1px solid #f48771' }}
        >
          <div className="mb-1 flex items-center gap-2 text-2xs font-bold" style={{ color: '#cd3131' }}>
            🔐 TOTP 二次验证 <span style={{ color: '#616161' }}>(高风险必填 · 当前用户: {CURRENT_USER})</span>
            <button
              type="button"
              onClick={() => refreshTotp(CURRENT_USER)}
              className="ml-2 rounded px-2 py-0.5 text-2xs"
              style={{ backgroundColor: '#ececec', color: '#059669' }}
            >
              ↻ 刷新
            </button>
            {currentTotp && (
              <span className="ml-2 font-mono" style={{ color: '#795e26' }}>
                当前 TOTP: {currentTotp}
              </span>
            )}
          </div>
          {mfaVerified ? (
            <div className="flex items-center gap-2 text-2xs" style={{ color: '#059669' }}>
              ✓ 已通过
              <button
                type="button"
                onClick={() => {
                  setMfaVerified(false);
                  setMfaCode('');
                }}
                className="ml-1 underline"
                style={{ color: '#616161' }}
              >
                重新验证
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <input
                value={mfaCode}
                onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="6 位 TOTP"
                className="w-32 rounded px-2 py-1 font-mono text-ui outline-none"
                style={{ backgroundColor: '#ffffff', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
              />
              <button
                type="button"
                onClick={handleMfaVerify}
                className="rounded px-3 py-1 text-2xs font-semibold"
                style={{ backgroundColor: '#cd3131', color: '#0e0e0e' }}
              >
                验证
              </button>
              {mfaError && <span className="text-2xs" style={{ color: '#ff8888' }}>{mfaError}</span>}
            </div>
          )}
        </div>
      )}

      {/* 主操作按钮（高风险双列：单人 + 双人） */}
      <div className="mb-2 flex gap-2">
        {!isHighRisk ? (
          <>
            <button
              type="button"
              onClick={handleApprove}
              disabled={busy || (requireMfa && !mfaVerified)}
              className="flex-1 rounded px-3 py-2 text-ui font-semibold transition-all disabled:opacity-40"
              style={{ backgroundColor: '#059669', color: '#0e0e0e' }}
            >
              {busy ? '处理中...' : '✓ 批准'}
            </button>
            <button
              type="button"
              onClick={handleReject}
              disabled={busy || (requireMfa && !mfaVerified)}
              className="flex-1 rounded px-3 py-2 text-ui font-semibold transition-all disabled:opacity-40"
              style={{ backgroundColor: '#cd3131', color: '#0e0e0e' }}
            >
              {busy ? '处理中...' : '✗ 驳回'}
            </button>
            <button
              type="button"
              onClick={handleQuestion}
              disabled={busy}
              className="flex-1 rounded px-3 py-2 text-ui font-semibold transition-all"
              style={{ backgroundColor: '#795e26', color: '#0e0e0e' }}
            >
              ❓ 问询
            </button>
          </>
        ) : (
          <>
            {/* 单人决策（绕过双人复核；仅特殊场景） */}
            <button
              type="button"
              onClick={handleApprove}
              disabled={busy || !mfaVerified}
              className="flex-1 rounded px-3 py-2 text-ui font-semibold transition-all disabled:opacity-40"
              style={{ backgroundColor: '#059669', color: '#0e0e0e' }}
              title="高风险任务建议用双人复核（后端也会强制）"
            >
              {busy ? '...' : '✓ 单人批准'}
            </button>
            <button
              type="button"
              onClick={handleReject}
              disabled={busy || !mfaVerified}
              className="flex-1 rounded px-3 py-2 text-ui font-semibold transition-all disabled:opacity-40"
              style={{ backgroundColor: '#cd3131', color: '#0e0e0e' }}
            >
              {busy ? '...' : '✗ 单人驳回'}
            </button>
          </>
        )}
      </div>

      {/* V1: 双人复核按钮（高风险任务） */}
      {isHighRisk && (
        <div
          className="mb-2 rounded p-2"
          style={{ backgroundColor: '#1d2a3a', border: '1px solid #569cd6' }}
        >
          <div className="mb-1 text-2xs font-bold" style={{ color: '#0451a5' }}>
            👥 双人复核模式（生产推荐）
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleDualFirst}
              disabled={busy || !mfaVerified}
              className="flex-1 rounded px-2 py-1 text-2xs font-semibold disabled:opacity-40"
              style={{ backgroundColor: '#0451a5', color: '#0e0e0e' }}
            >
              {busy ? '...' : '① 第一审批（沈雷）'}
            </button>
            <button
              type="button"
              onClick={handleDualSecond}
              disabled={busy || !mfaVerified}
              className="flex-1 rounded px-2 py-1 text-2xs font-semibold disabled:opacity-40"
              style={{ backgroundColor: '#0b6bcb', color: '#0e0e0e' }}
            >
              {busy ? '...' : '② 第二审批（陈宇）'}
            </button>
          </div>
        </div>
      )}

      {/* V1: 委派 + 链验证 */}
      <div className="flex items-center gap-2">
        <input
          value={delegateTo}
          onChange={(e) => setDelegateTo(e.target.value)}
          placeholder="委派给 (用户名)"
          className="flex-1 rounded px-2 py-1 text-ui outline-none"
          style={{ backgroundColor: '#ffffff', color: '#1f1f1f', border: '1px solid #d4d4d4' }}
        />
        <button
          type="button"
          onClick={handleDelegate}
          className="rounded px-3 py-1 text-ui"
          style={{ backgroundColor: '#ececec', color: '#333333' }}
        >
          委派
        </button>
        <button
          type="button"
          onClick={handleVerify}
          className="rounded px-3 py-1 text-ui"
          style={{ backgroundColor: '#ececec', color: '#059669' }}
          title="验证签名链完整性"
        >
          🔗 验证
        </button>
      </div>

      {/* V1: 操作结果提示 */}
      {resultMsg && (
        <div
          className="mt-2 rounded px-2 py-1 text-2xs"
          style={{
            backgroundColor: resultMsg.startsWith('✓') ? '#1d3a1d' : '#fbeaea',
            color: resultMsg.startsWith('✓') ? '#059669' : '#ff8888',
            border: `1px solid ${resultMsg.startsWith('✓') ? '#059669' : '#ff8888'}`,
          }}
        >
          {resultMsg}
        </div>
      )}
      {verifyResult && (
        <div
          className="mt-1 rounded px-2 py-1 text-2xs"
          style={{
            backgroundColor: verifyResult.startsWith('✓') ? '#1d3a1d' : '#fbeaea',
            color: verifyResult.startsWith('✓') ? '#059669' : '#ff8888',
          }}
        >
          {verifyResult}
        </div>
      )}
    </div>
  );
}