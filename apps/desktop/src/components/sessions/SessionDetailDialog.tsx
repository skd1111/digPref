/**
 * SessionDetailDialog —— Phase 6 V1.5 会话详情弹窗。
 *
 * 包含 5 个 Tab：
 *   1. 概览（stats）
 *   2. 消息列表（只读）
 *   3. 分支列表 + 创建分支
 *   4. 共享管理（share tokens + permissions）
 *   5. 事件哈希链（verify integrity）
 */
import { useEffect, useState } from 'react';
import { useSessionsStore } from '@/store/sessionsStore';
import { BranchDialog } from './BranchDialog';
import { ExportDialog } from './ExportDialog';
import { EventGraphViz } from './EventGraphViz';

type Tab = 'overview' | 'messages' | 'branches' | 'sharing' | 'chain';

interface Props {
  sessionId: string;
  open: boolean;
  onClose: () => void;
}

export function SessionDetailDialog({ sessionId, open, onClose }: Props): JSX.Element | null {
  const get = useSessionsStore((s) => s.get);
  const activeSessionDetail = useSessionsStore((s) => s.activeSessionDetail);
  const loadStats = useSessionsStore((s) => s.loadStats);
  const stats = useSessionsStore((s) => s.stats[sessionId]);
  const branchesList = useSessionsStore((s) => s.branchesList);
  const branches = useSessionsStore((s) => s.branches[sessionId] ?? []);
  const shareList = useSessionsStore((s) => s.shareList);
  const shareInfo = useSessionsStore((s) => s.shareInfo[sessionId]);
  const loadEventChain = useSessionsStore((s) => s.loadEventChain);
  const verifyEventChain = useSessionsStore((s) => s.verifyEventChain);
  const eventChains = useSessionsStore((s) => s.eventChains[sessionId] ?? []);
  const shareCreate = useSessionsStore((s) => s.shareCreate);
  const shareRevoke = useSessionsStore((s) => s.shareRevoke);
  const shareGrant = useSessionsStore((s) => s.shareGrant);

  const [tab, setTab] = useState<Tab>('overview');
  const [showBranchDialog, setShowBranchDialog] = useState(false);
  const [showExportDialog, setShowExportDialog] = useState(false);
  const [chainVerified, setChainVerified] = useState<{ valid: boolean; broken_reason: string | null } | null>(null);
  const [grantTarget, setGrantTarget] = useState('');
  const [grantPerm, setGrantPerm] = useState<'read' | 'write'>('read');
  const [sharePerm, setSharePerm] = useState<'read' | 'write'>('read');
  const [shareExpiry, setShareExpiry] = useState('');

  useEffect(() => {
    if (open && sessionId) {
      void get(sessionId);
      void loadStats(sessionId);
      void branchesList(sessionId);
      void shareList(sessionId);
      void loadEventChain(sessionId);
    }
  }, [open, sessionId, get, loadStats, branchesList, shareList, loadEventChain]);

  if (!open) return null;
  const detail = activeSessionDetail?.id === sessionId ? activeSessionDetail : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-[900px] flex-col rounded shadow-xl"
        style={{ backgroundColor: '#ffffff', color: '#333333' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div
          className="flex items-center justify-between px-4 py-3"
          style={{ borderBottom: '1px solid #c0c0c0' }}
        >
          <div>
            <h2 className="text-lg font-semibold">{detail?.title ?? '会话详情'}</h2>
            <div className="text-xs" style={{ color: '#616161' }}>
              {detail?.id ?? sessionId} · {detail?.owner} ·{' '}
              {detail?.parent_session_id ? `分支自 ${detail.parent_session_id.slice(0, 8)}` : '主会话'}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1 text-sm hover:bg-[#333]"
          >
            ✕
          </button>
        </div>

        {/* Tab 切换 */}
        <div className="flex" style={{ borderBottom: '1px solid #c0c0c0' }}>
          {(['overview', 'messages', 'branches', 'sharing', 'chain'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className="px-4 py-2 text-xs hover:bg-[#2a2d2e]"
              style={{
                backgroundColor: tab === t ? '#0e639c' : 'transparent',
                borderBottom: tab === t ? '2px solid #007acc' : '2px solid transparent',
              }}
            >
              {t === 'overview' && '概览'}
              {t === 'messages' && `消息 (${detail?.messages.length ?? 0})`}
              {t === 'branches' && `分支 (${branches.length})`}
              {t === 'sharing' && `共享 (${shareInfo?.tokens.length ?? 0})`}
              {t === 'chain' && `事件链 (${eventChains.length})`}
            </button>
          ))}
        </div>

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto p-4 text-sm">
          {tab === 'overview' && (
            <div>
              <h3 className="mb-2 font-semibold">会话统计</h3>
              {stats ? (
                <div className="grid grid-cols-2 gap-2">
                  <Stat label="消息数" value={stats.message_count} />
                  <Stat label="检查点数" value={stats.checkpoint_count} />
                  <Stat label="事件链长度" value={stats.event_chain_count} />
                  <Stat label="压缩日志数" value={stats.compression_count} />
                  <Stat label="分支数" value={stats.branch_count} />
                  <Stat label="状态" value={stats.status} />
                  {stats.is_branch && <Stat label="分支标签" value={stats.branch_label || '(无)'} />}
                </div>
              ) : (
                <div className="text-xs" style={{ color: '#616161' }}>加载中...</div>
              )}
              <div className="mt-4 flex gap-2">
                <button
                  type="button"
                  onClick={() => setShowBranchDialog(true)}
                  className="rounded px-3 py-1 text-xs"
                  style={{ backgroundColor: '#0e639c', color: '#fff' }}
                >
                  🔀 创建分支
                </button>
                <button
                  type="button"
                  onClick={() => setShowExportDialog(true)}
                  className="rounded px-3 py-1 text-xs"
                  style={{ backgroundColor: '#0e639c', color: '#fff' }}
                >
                  💾 加密导出
                </button>
              </div>
            </div>
          )}

          {tab === 'messages' && (
            <div className="space-y-2">
              {(detail?.messages ?? [])
                .filter((m) => !String(m.content ?? '').startsWith('（mock'))
                .map((m, idx) => (
                <div
                  key={idx}
                  className="rounded p-2 text-xs"
                  style={{ backgroundColor: '#f3f3f3', border: '1px solid #c0c0c0' }}
                >
                  <div className="mb-1 flex justify-between" style={{ color: '#616161' }}>
                    <span className="font-mono">{String(m.role)}</span>
                    <span>{new Date(Number(m.created_at)).toLocaleString()}</span>
                  </div>
                  <div className="whitespace-pre-wrap break-words">{String(m.content)}</div>
                  {Boolean(m.tool_name) && (
                    <div className="mt-1" style={{ color: '#0b6bcb' }}>
                      🔧 {String(m.tool_name)}
                    </div>
                  )}
                </div>
              )) ?? <div>暂无消息</div>}
            </div>
          )}

          {tab === 'branches' && (
            <div>
              <button
                type="button"
                onClick={() => setShowBranchDialog(true)}
                className="mb-3 rounded px-3 py-1 text-xs"
                style={{ backgroundColor: '#0e639c', color: '#fff' }}
              >
                + 新建分支
              </button>
              {branches.length === 0 && <div className="text-xs" style={{ color: '#616161' }}>暂无分支</div>}
              {branches.map((b) => (
                <div
                  key={b.id}
                  className="rounded p-2 mb-2 text-xs"
                  style={{ backgroundColor: '#f3f3f3', border: '1px solid #c0c0c0' }}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">🔀 {b.title}</span>
                    <span style={{ color: '#616161' }}>{b.branch_label}</span>
                  </div>
                  <div style={{ color: '#616161' }} className="mt-1">
                    从 {b.branch_from_checkpoint_id ?? '末尾'} 分支 ·{' '}
                    {new Date(b.created_at).toLocaleString()}
                  </div>
                </div>
                ))}
            </div>
          )}

          {tab === 'sharing' && (
            <div>
              <h3 className="mb-2 font-semibold">分享令牌</h3>
              <div className="mb-3 flex items-end gap-2">
                <select
                  value={sharePerm}
                  onChange={(e) => setSharePerm(e.target.value as 'read' | 'write')}
                  className="rounded px-2 py-1 text-xs"
                  style={{ backgroundColor: '#ececec', color: '#fff' }}
                >
                  <option value="read">只读</option>
                  <option value="write">可写</option>
                </select>
                <input
                  type="number"
                  value={shareExpiry}
                  onChange={(e) => setShareExpiry(e.target.value)}
                  placeholder="过期毫秒（可选）"
                  className="flex-1 rounded px-2 py-1 text-xs"
                  style={{ backgroundColor: '#ececec', color: '#fff' }}
                />
                <button
                  type="button"
                  onClick={async () => {
                    const expiryMs = shareExpiry ? Number(shareExpiry) : undefined;
                    const tok = await shareCreate(sessionId, {
                      permission: sharePerm,
                      ...(expiryMs !== undefined && { expires_in_ms: expiryMs }),
                    });
                    if (tok) {
                      await shareList(sessionId);
                    }
                  }}
                  className="rounded px-3 py-1 text-xs"
                  style={{ backgroundColor: '#0e639c', color: '#fff' }}
                >
                  + 创建
                </button>
              </div>
              {shareInfo?.tokens.length === 0 && (
                <div className="text-xs" style={{ color: '#616161' }}>暂无分享令牌</div>
              )}
              {shareInfo?.tokens.map((t) => (
                <div
                  key={t.token}
                  className="rounded p-2 mb-2 text-xs"
                  style={{ backgroundColor: '#f3f3f3', border: '1px solid #c0c0c0' }}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono">{t.token.slice(0, 16)}…</span>
                    <button
                      type="button"
                      onClick={async () => {
                        await shareRevoke(sessionId, t.token);
                        await shareList(sessionId);
                      }}
                      className="text-xs hover:underline"
                      style={{ color: '#cd3131' }}
                    >
                      撤销
                    </button>
                  </div>
                  <div style={{ color: '#616161' }}>
                    {t.permission} · {t.expires_at ? `过期 ${new Date(t.expires_at).toLocaleString()}` : '永不过期'}
                  </div>
                </div>
              ))}

              <h3 className="mt-4 mb-2 font-semibold">权限矩阵</h3>
              <div className="mb-3 flex items-end gap-2">
                <input
                  type="text"
                  value={grantTarget}
                  onChange={(e) => setGrantTarget(e.target.value)}
                  placeholder="目标用户"
                  className="flex-1 rounded px-2 py-1 text-xs"
                  style={{ backgroundColor: '#ececec', color: '#fff' }}
                />
                <select
                  value={grantPerm}
                  onChange={(e) => setGrantPerm(e.target.value as 'read' | 'write')}
                  className="rounded px-2 py-1 text-xs"
                  style={{ backgroundColor: '#ececec', color: '#fff' }}
                >
                  <option value="read">read</option>
                  <option value="write">write</option>
                </select>
                <button
                  type="button"
                  onClick={async () => {
                    if (!grantTarget.trim()) return;
                    await shareGrant(sessionId, { target_actor: grantTarget.trim(), permission: grantPerm });
                    await shareList(sessionId);
                    setGrantTarget('');
                  }}
                  className="rounded px-3 py-1 text-xs"
                  style={{ backgroundColor: '#0e639c', color: '#fff' }}
                >
                  授权
                </button>
              </div>
              {shareInfo?.permissions &&
                Object.entries(shareInfo.permissions).map(([actor, perm]) => (
                  <div key={actor} className="rounded px-2 py-1 text-xs mb-1" style={{ backgroundColor: '#f3f3f3' }}>
                    {actor}: <span style={{ color: '#0b6bcb' }}>{perm}</span>
                  </div>
                ))}
            </div>
          )}

          {tab === 'chain' && (
            <div>
              <div className="mb-3 flex items-center gap-2">
                <button
                  type="button"
                  onClick={async () => {
                    const r = await verifyEventChain(sessionId);
                    setChainVerified(r);
                  }}
                  className="rounded px-3 py-1 text-xs"
                  style={{ backgroundColor: '#0e639c', color: '#fff' }}
                >
                  🔐 验证哈希链
                </button>
                {chainVerified && (
                  <span
                    className="text-xs"
                    style={{ color: chainVerified.valid ? '#6a9955' : '#cd3131' }}
                  >
                    {chainVerified.valid
                      ? '✓ 完整'
                      : `✗ 断裂：${chainVerified.broken_reason ?? '未知'}`}
                  </span>
                )}
              </div>
              <EventGraphViz entries={eventChains} />
              <div className="mt-4 space-y-1">
                {eventChains.map((e) => (
                  <div
                    key={e.id}
                    className="rounded p-2 text-xs"
                    style={{ backgroundColor: '#f3f3f3', border: '1px solid #c0c0c0' }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono">#{e.id} {e.event_type}</span>
                      <span style={{ color: '#616161' }}>{e.actor}</span>
                    </div>
                    <div className="font-mono text-xs" style={{ color: '#616161' }}>
                      hash: {e.hash.slice(0, 16)}…
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {showBranchDialog && (
          <BranchDialog
            sessionId={sessionId}
            open={showBranchDialog}
            onClose={() => setShowBranchDialog(false)}
          />
        )}
        {showExportDialog && (
          <ExportDialog
            sessionId={sessionId}
            open={showExportDialog}
            onClose={() => setShowExportDialog(false)}
          />
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }): JSX.Element {
  return (
    <div className="rounded p-2" style={{ backgroundColor: '#f3f3f3', border: '1px solid #c0c0c0' }}>
      <div className="text-xs" style={{ color: '#616161' }}>{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}
