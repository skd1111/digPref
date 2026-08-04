/**
 * ShareDialog —— 分享上下文到企微 / 钉钉 / 飞书的占位弹窗（V0 MVP）。
 *
 * 行为：
 *   - 显示卡片预览（标题 / 摘要 / 评论数 / 参与者）
 *   - 生成 Deep Link `eaide://collab/open?context=...&token=...&ts=...`（5 分钟一次性）
 *   - 真实 IM Webhook 推送留到 Phase 8 接入
 *   - 三个按钮（企微 / 钉钉 / 飞书）目前均触发"复制 Deep Link + 显示"分享功能即将上线"
 */
import { useState } from 'react';
import { useCollabStore, formatRelativeTime } from '@/store/collabStore';
import { ANCHOR_LABELS, USER_BY_ID } from '@/types/collab';

interface ShareDialogProps {
  contextId: string;
  onClose: () => void;
}

const IM_OPTIONS = [
  { key: 'wecom', label: '企微', icon: '💬', color: '#07c160' },
  { key: 'dingtalk', label: '钉钉', icon: '📌', color: '#0089ff' },
  { key: 'feishu', label: '飞书', icon: '🚀', color: '#3370ff' },
] as const;

export function ShareDialog({ contextId, onClose }: ShareDialogProps): JSX.Element {
  const ctx = useCollabStore((s) => s.contexts.find((c) => c.id === contextId));
  const generateDeepLink = useCollabStore((s) => s.generateDeepLink);
  const [deepLink, setDeepLink] = useState<string | null>(null);
  const [chosen, setChosen] = useState<typeof IM_OPTIONS[number]['key'] | null>(null);
  const [copied, setCopied] = useState(false);

  if (!ctx) return <></>;

  const meta = ANCHOR_LABELS[ctx.anchor_type];

  const handleShare = (key: typeof IM_OPTIONS[number]['key']): void => {
    setChosen(key);
    const link = generateDeepLink(ctx.id);
    setDeepLink(link);
  };

  const handleCopy = async (): Promise<void> => {
    if (!deepLink) return;
    try {
      await navigator.clipboard.writeText(deepLink);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.5)' }}
      onClick={onClose}
    >
      <div
        className="w-[480px] max-w-[90vw] rounded-lg border shadow-2xl"
        style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题 */}
        <div
          className="flex items-center justify-between border-b px-4 py-3"
          style={{ borderColor: '#d4d4d4' }}
        >
          <h3 className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
            📤 分享到企业 IM
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-0.5 text-2xs transition-colors"
            style={{ color: '#616161' }}
          >
            ✕
          </button>
        </div>

        {/* 卡片预览 */}
        <div className="p-4">
          <div
            className="rounded-md border p-3"
            style={{ backgroundColor: '#ffffff', borderColor: '#d4d4d4' }}
          >
            <div className="mb-2 flex items-center gap-2">
              <span
                className="rounded px-1.5 py-0.5 text-2xs font-semibold"
                style={{ backgroundColor: meta.color, color: '#0e0e0e' }}
              >
                {meta.icon} {meta.label}
              </span>
              <span className="text-2xs" style={{ color: '#616161' }}>
                {ctx.target_env ? `[${ctx.target_env}]` : ''}
              </span>
            </div>
            <div className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
              {ctx.title}
            </div>
            <div className="mt-1 line-clamp-2 text-2xs" style={{ color: '#a0a0a0' }}>
              {ctx.summary}
            </div>
            <div className="mt-2 flex items-center gap-2 text-2xs" style={{ color: '#616161' }}>
              <div className="flex -space-x-1.5">
                {ctx.participant_names.slice(0, 4).map((name, idx) => {
                  const u = USER_BY_ID[ctx.participants[idx]];
                  return (
                    <span
                      key={ctx.participants[idx]}
                      className="flex h-5 w-5 items-center justify-center rounded-full text-2xs font-semibold text-white"
                      style={{
                        backgroundColor: u?.avatar_color ?? '#616161',
                        boxShadow: '0 0 0 1px #d0d0d0',
                      }}
                    >
                      {name.charAt(0)}
                    </span>
                  );
                })}
              </div>
              <span>{ctx.participant_names.length} 参与者</span>
              <span>·</span>
              <span>💬 {ctx.comment_count} 条讨论</span>
              <span>·</span>
              <span>{formatRelativeTime(ctx.updated_at)}</span>
            </div>
            {deepLink && (
              <div
                className="mt-3 rounded border p-2 text-2xs"
                style={{
                  backgroundColor: '#0e0e0e',
                  borderColor: '#d4d4d4',
                  color: '#0451a5',
                  wordBreak: 'break-all',
                  fontFamily: 'ui-monospace, monospace',
                }}
              >
                {deepLink}
              </div>
            )}
            {deepLink && (
              <div
                className="mt-2 rounded p-2 text-2xs"
                style={{
                  backgroundColor: 'rgba(99, 102, 241, 0.10)',
                  border: '1px solid #6366f1',
                  color: '#4f46e5',
                }}
              >
                💡 收件人点击链接 → OS 唤起本地 EAIDE → 自动跳转到讨论页。
                Deep Link 一次性 token，5 分钟内有效。
              </div>
            )}
          </div>

          {/* IM 选择 */}
          <div className="mt-3 grid grid-cols-3 gap-2">
            {IM_OPTIONS.map((im) => (
              <button
                key={im.key}
                type="button"
                onClick={() => handleShare(im.key)}
                disabled={!!chosen}
                className="flex flex-col items-center gap-1 rounded-md border py-3 transition-transform hover:scale-[1.02]"
                style={{
                  backgroundColor: chosen === im.key ? `${im.color}22` : 'transparent',
                  borderColor: chosen === im.key ? im.color : '#1f1f1f',
                  color: chosen === im.key ? im.color : '#1f1f1f',
                  cursor: chosen ? 'default' : 'pointer',
                }}
              >
                <span className="text-2xl">{im.icon}</span>
                <span className="text-2xs">{im.label}</span>
              </button>
            ))}
          </div>

          {/* 提示 + 操作 */}
          {deepLink ? (
            <div className="mt-3 flex justify-between gap-2">
              <button
                type="button"
                onClick={handleCopy}
                className="rounded px-3 py-1 text-2xs transition-colors"
                style={{
                  backgroundColor: '#6366f1',
                  color: '#ffffff',
                }}
              >
                {copied ? '✓ 已复制' : '📋 复制 Deep Link'}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="rounded px-3 py-1 text-2xs transition-colors"
                style={{
                  backgroundColor: 'transparent',
                  color: '#616161',
                  border: '1px solid #d4d4d4',
                }}
              >
                完成
              </button>
            </div>
          ) : (
            <div
              className="mt-3 rounded p-2 text-center text-2xs"
              style={{
                backgroundColor: 'rgba(220, 220, 170, 0.10)',
                color: '#795e26',
              }}
            >
              ⚠ 分享功能即将上线（Phase 8 集成企微/钉钉/飞书 Webhook）。当前 MVP 可生成 Deep Link 供测试。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
