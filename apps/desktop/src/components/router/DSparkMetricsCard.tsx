/**
 * DSparkMetricsCard —— Phase 13 V0 加速效果监控卡。
 *
 * 挂在 RouterDashboard 末尾（紧跟 Spark toggle 之后）。
 *
 * 展示 4 类指标：
 *   - 本次会话决策数 + 启用率（来自 /dspark/config.stats，**重启归零**）
 *   - 类别分布（横向条）
 *   - 决策原因分布（最近 100 条）
 *   - 最近 5 条决策时间线
 *
 * V0 不做实时刷新（5 秒轮询可以 V1 加）。
 *
 * 类型全部来自 @eaide/shared-protocol/dspark（与 FastAPI Pydantic + Rust serde 对齐）。
 * 文档：[docs/design/phase-13-dspark.md](../../../../docs/design/phase-13-dspark.md)
 */
import { useEffect, useState } from 'react';
import { REASON_COLOR, type DSparkDecisionRecord } from '@eaide/shared-protocol';
import { ipc } from '@/ipc/invoke';

const CATEGORY_COLOR: Record<string, string> = {
  intent: '#cd3131',
  repair: '#cd3131',
  skill_router: '#cd3131',
  data_summary: '#cd3131',
  plan: '#0451a5',
  summarise: '#0451a5',
  sql_generation: '#795e26',
  code_completion: '#795e26',
  code_explanation: '#c586c0',
  log_analysis: '#c586c0',
  chat_qa: '#059669',
  toolspec: '#0451a5',
};

export function DSparkMetricsCard(): JSX.Element {
  const [stats, setStats] = useState<{
    total_decisions: number;
    dspark_enabled_pct: number;
    per_category: Record<string, number>;
    per_reason: Record<string, number>;
  } | null>(null);
  const [recent, setRecent] = useState<DSparkDecisionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = async (): Promise<void> => {
    setLoading(true);
    setErr(null);
    try {
      const cfg = await ipc.dsparkGetConfig();
      const rec = await ipc.dsparkGetRecent(5);
      setStats(cfg.stats);
      setRecent(rec);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const total = stats?.total_decisions ?? 0;
  const enabledPct = stats?.dspark_enabled_pct ?? 0;
  const enabledColor = enabledPct > 50 ? '#059669' : enabledPct > 20 ? '#795e26' : '#616161';

  return (
    <div
      className="rounded border p-4"
      style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4' }}
    >
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          ⚡ DSpark 推测解码（V0）
        </h3>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="rounded px-2 py-0.5 text-2xs"
          style={{
            backgroundColor: '#ececec',
            color: loading ? '#616161' : '#333333',
            cursor: loading ? 'wait' : 'pointer',
          }}
        >
          {loading ? '⟳' : '刷新'}
        </button>
      </div>

      <p className="mb-3 text-2xs" style={{ color: '#616161' }}>
        V0 决策层（注入 RouteDecision）。V1 才会跑 llama.cpp 加速。
        <span style={{ color: '#795e26' }}> 决策数据为本次会话内存，重启归零</span>。
      </p>

      {err && (
        <div
          className="mb-3 rounded px-2 py-1 text-2xs"
          style={{ backgroundColor: '#3c1e1e', color: '#cd3131' }}
        >
          {err}
        </div>
      )}

      {/* 关键 3 指标 */}
      <div className="mb-3 grid grid-cols-3 gap-2">
        <StatBox title="本次会话决策" value={String(total)} color="#9cdcfe" />
        <StatBox title="启用率" value={`${enabledPct.toFixed(1)}%`} color={enabledColor} />
        <StatBox
          title="关闭原因"
          value={String(Object.keys(stats?.per_reason ?? {}).filter((r) => r.startsWith('off-')).length)}
          color="#616161"
        />
      </div>

      {/* 类别分布 */}
      {stats && Object.keys(stats.per_category).length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#616161' }}>
            任务类别分布
          </div>
          <div className="space-y-1">
            {Object.entries(stats.per_category)
              .sort((a, b) => b[1] - a[1])
              .map(([cat, count]) => {
                const pct = total > 0 ? (count / total) * 100 : 0;
                return (
                  <div key={cat} className="flex items-center gap-2 text-2xs">
                    <span className="w-32 truncate font-mono" style={{ color: CATEGORY_COLOR[cat] ?? '#1f1f1f' }}>
                      {cat}
                    </span>
                    <div
                      className="h-2 rounded"
                      style={{
                        width: `${Math.max(pct, 2)}%`,
                        backgroundColor: CATEGORY_COLOR[cat] ?? '#ececec',
                      }}
                    />
                    <span style={{ color: '#616161' }}>×{count}</span>
                  </div>
                );
              })}
          </div>
        </div>
      )}

      {/* 决策原因分布 */}
      {stats && Object.keys(stats.per_reason).length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#616161' }}>
            关闭原因分布
          </div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(stats.per_reason).map(([reason, count]) => {
              const color = (REASON_COLOR as Record<string, string>)[reason] ?? '#616161';
              return (
                <span
                  key={reason}
                  className="rounded px-1.5 py-0.5 text-2xs"
                  style={{
                    backgroundColor: '#ffffff',
                    color,
                    borderLeft: `3px solid ${color}`,
                  }}
                >
                  {reason} × {count}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* 最近 5 条决策 */}
      {recent.length > 0 && (
        <div>
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider" style={{ color: '#616161' }}>
            最近 5 条决策
          </div>
          <div className="space-y-1">
            {recent.map((r, i) => {
              const reasonColor = (REASON_COLOR as Record<string, string>)[r.reason] ?? '#ececec';
              return (
                <div
                  key={i}
                  className="flex items-center gap-2 rounded px-2 py-1 text-2xs"
                  style={{ backgroundColor: '#ffffff' }}
                >
                  <span style={{ color: r.speculative_enabled ? '#059669' : '#616161' }}>
                    {r.speculative_enabled ? '⚡' : '○'}
                  </span>
                  <span className="font-mono" style={{ color: CATEGORY_COLOR[r.task_category] ?? '#0b6bcb' }}>
                    {r.task_category}
                  </span>
                  <span style={{ color: '#616161' }}>
                    K={r.n_draft} p={r.draft_p_min.toFixed(2)}
                  </span>
                  <span
                    className="ml-auto rounded px-1.5"
                    style={{
                      backgroundColor: reasonColor,
                      color: '#0e0e0e',
                    }}
                  >
                    {r.reason}
                  </span>
                  <span style={{ color: '#616161' }}>
                    {new Date(r.ts * 1000).toLocaleTimeString()}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {total === 0 && (
        <div className="py-2 text-center text-2xs" style={{ color: '#616161' }}>
          暂无决策记录。试着在主对话输入「SELECT 哪些语句？」触发 sql_generation 类别。
        </div>
      )}
    </div>
  );
}

function StatBox({ title, value, color }: { title: string; value: string; color: string }): JSX.Element {
  return (
    <div
      className="rounded p-2 text-center"
      style={{ backgroundColor: '#ffffff' }}
    >
      <div className="text-2xs" style={{ color: '#616161' }}>
        {title}
      </div>
      <div className="mt-0.5 font-mono text-lg font-semibold" style={{ color }}>
        {value}
      </div>
    </div>
  );
}
