/**
 * ScoringRadar —— Phase 2C V2.5 收尾：5 维评分可视化（零依赖）。
 *
 * V2.5-3 收尾：用 inline 横向柱状条 + 文本把 5 维评分渲染为
 * "雷达图风格"。零外部依赖（不引入 Chart.js / recharts）。
 *
 * 颜色编码：绿 ≥ 0.66 / 黄 0.33-0.65 / 红 < 0.33。
 */
import { useRouterStore } from '@/store/routerStore';

const DIM_LABELS: Record<string, string> = {
  capability: '能力',
  cost: '成本',
  latency: '延迟',
  compliance: '合规',
  availability: '可用',
};

interface ScoringRadarProps {
  /** 0-1 范围 5 维评分；缺字段按 0 显示 */
  scores?: Partial<Record<string, number>>;
  /** 顶部标题（默认 "📊 5 维评分"） */
  title?: string;
}

export function ScoringRadar({
  scores,
  title = '📊 5 维评分（当前引擎权重）',
}: ScoringRadarProps): JSX.Element {
  const weights = useRouterStore((s) => s.weights);

  // 渲染 5 行：维度名 + 横向 bar（绿色填充）+ 权重（用户设置）
  return (
    <div
      className="rounded border p-3 my-3"
      style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff' }}
      data-testid="scoring-radar"
    >
      <div className="text-2xs font-semibold mb-2" style={{ color: '#795e26' }}>
        {title}
      </div>
      <div className="space-y-1.5">
        {Object.keys(DIM_LABELS).map((dim) => {
          const score = scores?.[dim] ?? 0;
          const weight = weights?.[dim as keyof typeof weights] ?? 0;
          // Clamp to [0, 1] for both visual bar width and display text
          const clampedScore = Math.max(0, Math.min(1, score));
          const clampedWeight = Math.max(0, Math.min(1, weight));
          const pct = clampedScore * 100;
          const wPct = clampedWeight * 100;
          return (
            <div key={dim} className="flex items-center gap-2 text-2xs">
              <div
                className="w-12 shrink-0 text-right"
                style={{ color: '#0b6bcb' }}
              >
                {DIM_LABELS[dim]}
              </div>
              <div
                className="relative h-2 flex-1 rounded"
                style={{ backgroundColor: '#f3f3f3' }}
              >
                <div
                  className="absolute left-0 top-0 h-2 rounded"
                  style={{
                    width: `${pct}%`,
                    backgroundColor:
                      pct >= 66 ? '#059669' : pct >= 33 ? '#795e26' : '#cd3131',
                  }}
                />
                {/* 权重刻度线 */}
                {weight > 0 && (
                  <div
                    className="absolute top-[-2px] h-[12px] w-px"
                    style={{
                      left: `${wPct}%`,
                      backgroundColor: '#0451a5',
                    }}
                    title={`权重 ${clampedWeight.toFixed(2)}`}
                  />
                )}
              </div>
              <div
                className="w-16 shrink-0 text-right font-mono"
                style={{ color: '#1f1f1f' }}
              >
                {clampedScore.toFixed(2)} · 权重={clampedWeight.toFixed(2)}
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-2xs mt-2" style={{ color: '#616161' }}>
        蓝线 = 当前引擎权重；bar 颜色 = 得分（绿 ≥ 0.66 / 黄 0.33-0.65 / 红 &lt; 0.33）。
      </p>
    </div>
  );
}