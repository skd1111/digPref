/**
 * TokenUsageBadge —— 「Agent: 就绪」旁的 Token 用量徽章 + 悬浮明细卡片。
 *
 * 徽章本体：速率（↑/↓）+ 调用次数，始终可见。
 * 鼠标悬浮：旁边弹出小卡片展示全部明细（当日累计 tokens / 调用次数 /
 * 费用总与按模型明细），鼠标离开即隐藏（纯 CSS group-hover，无状态）。
 * 数据来自 useTokenUsage（2s 轮询 GET /llm/token-usage）。
 *
 * placement：卡片弹出方向 —— StatusBar 用 'top'（向上弹），TopBar 用
 * 'bottom'（向下弹）。
 *
 * 定位修复（2026-08-10）：悬浮卡改用 fixed 定位 + 视口边界钳制。
 * 旧实现用 absolute，徽章靠窗口边缘时卡片会撑出文档滚动区，
 * 导致 hover 时右侧/底部凭空出现滚动条；fixed 元素不参与文档滚动溢出，
 * 彻底消除该问题。
 */
import { useRef, useState } from 'react';
import { useTokenUsage } from '@/hooks/useTokenUsage';
import type { TokenUsageSnapshot } from '@/ipc/invoke';

/** 悬浮卡片宽度（w-72 = 288px），用于边界钳制计算 */
const CARD_WIDTH = 288;
/** 卡片与视口边缘的最小间距 */
const CARD_MARGIN = 8;

/** token 数量紧凑格式：999 → "999"，12345 → "12.3k"，2_300_000 → "2.3M"。 */
export function formatTokenCount(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '0';
  if (n < 1000) return String(Math.round(n));
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** 速率格式：0 → "0"，其余保留 1 位小数（<10）或取整（≥10）。 */
export function formatTokenRate(ratePerS: number): string {
  if (!Number.isFinite(ratePerS) || ratePerS <= 0) return '0';
  if (ratePerS < 10) return ratePerS.toFixed(1);
  return formatTokenCount(ratePerS);
}

/** 费用格式：小数保留 4 位（<0.01）或 2 位；非有限/负数 → 0。 */
export function formatCost(cost: number): string {
  if (!Number.isFinite(cost) || cost <= 0) return '0';
  if (cost < 0.01) return cost.toFixed(4);
  return cost.toFixed(2);
}

/** 悬浮卡片里的一行：左标签 + 右数值。 */
function Row({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex items-center justify-between gap-4">
      <span style={{ color: '#616161' }}>{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}

/** 悬浮明细卡片内容（hover 时展示，鼠标离开即隐藏）。 */
export function UsageDetailCard({ usage }: { usage: TokenUsageSnapshot }): JSX.Element {
  const modelRows = Object.entries(usage.cost_by_model);
  return (
    <div
      data-testid="token-usage-card"
      className="w-72 rounded border p-3 text-2xs leading-relaxed shadow-lg"
      style={{ backgroundColor: '#ffffff', borderColor: '#d0d0d0', color: '#1f1f1f' }}
    >
      <div className="mb-1 font-semibold">Token 用量 · {usage.day}</div>
      <Row
        label="实时速率（上传/下载）"
        value={`↑${formatTokenRate(usage.rate_upload_per_s)} / ↓${formatTokenRate(
          usage.rate_download_per_s,
        )} tok/s`}
      />
      <Row label="调用速率" value={`${usage.rate_calls_per_s} 次/s`} />
      <div className="my-1 border-t" style={{ borderColor: '#e5e5e5' }} />
      <Row
        label="当日 tokens（上传/下载）"
        value={`↑${formatTokenCount(usage.today_upload_tokens)} / ↓${formatTokenCount(
          usage.today_download_tokens,
        )}`}
      />
      <Row label="当日合计" value={`${formatTokenCount(usage.today_total_tokens)} tokens`} />
      <Row label="当日调用次数" value={`${usage.today_call_count} 次`} />
      <div className="my-1 border-t" style={{ borderColor: '#e5e5e5' }} />
      <Row label="当日费用（总）" value={formatCost(usage.today_cost_total)} />
      {modelRows.length > 0 ? (
        modelRows.map(([m, c]) => <Row key={m} label={`· ${m}`} value={formatCost(c)} />)
      ) : (
        <div style={{ color: '#8a8a8a' }}>本地/免费模型不计费</div>
      )}
      <div className="mt-1" style={{ color: '#8a8a8a' }}>
        速率为近 {usage.window_seconds}s 均值；当日统计自零点累计，重启不丢。
        单价取自模型管理 cost_per_1k_tokens。
      </div>
    </div>
  );
}

export function TokenUsageBadge({
  placement = 'top',
}: {
  /** 卡片弹出方向：StatusBar 向上（top），TopBar 向下（bottom） */
  placement?: 'top' | 'bottom';
}): JSX.Element {
  const usage = useTokenUsage();
  const badgeRef = useRef<HTMLSpanElement>(null);
  // 悬浮卡坐标：mouseenter 时按徽章位置计算（fixed 定位，钳制在视口内）
  const [cardPos, setCardPos] = useState<{ left: number } & (
    | { top: number; bottom?: undefined }
    | { bottom: number; top?: undefined }
  ) | null>(null);

  // 徽章本体：实时速率（↑上传/↓下载）+ 调用次数；当日总量与费用在悬浮卡片里
  const text = usage
    ? `↑${formatTokenRate(usage.rate_upload_per_s)} ↓${formatTokenRate(
        usage.rate_download_per_s,
      )} tok/s · ${formatTokenCount(usage.today_call_count)} 次`
    : '↑-- ↓-- tok/s';

  const handleEnter = (): void => {
    const rect = badgeRef.current?.getBoundingClientRect();
    if (!rect) return;
    // 横向：卡片右缘对齐徽章右缘，钳制在视口内（徽章靠边时不撑出横向滚动条）
    const left = Math.max(
      CARD_MARGIN,
      Math.min(rect.right - CARD_WIDTH, window.innerWidth - CARD_WIDTH - CARD_MARGIN),
    );
    // 纵向：StatusBar 向上弹（bottom 锚定），TopBar 向下弹（top 锚定）；
    // wrapper 紧贴徽章边缘，用自身 padding 桥接 6px 间隙（hover 不失焦）
    setCardPos(
      placement === 'top'
        ? { left, bottom: window.innerHeight - rect.top }
        : { left, top: rect.bottom },
    );
  };

  return (
    <span
      ref={badgeRef}
      className="group relative inline-flex items-center whitespace-nowrap tabular-nums"
      data-testid="token-usage-badge"
      onMouseEnter={handleEnter}
    >
      {text}
      {usage && (
        <div
          className={`fixed z-50 hidden group-hover:block ${
            placement === 'top' ? 'pb-1.5' : 'pt-1.5'
          }`}
          style={cardPos ?? undefined}
        >
          <UsageDetailCard usage={usage} />
        </div>
      )}
    </span>
  );
}
