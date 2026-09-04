/**
 * RouterDashboard —— Phase 2C V2 路由仪表盘（5 块组合）。
 *
 * V0: ScoringWeightsEditor + CircuitBreakerStatus + BudgetPanel + RunMode + Spark toggle + DSpark 指标卡。
 * V2 增量（Phase 2C V2.0）：
 *   - 5 秒轮询 ipc.routerGetMetrics() → 替换 routerStore 中的 mock 默认值
 *   - Spark toggle 调 ipc.routerSetSparkMode()（后端 LMRouter.set_spark_mode）
 *   - ScoringWeightsEditor 保存调 ipc.routerSetWeights()（PUT /router/weights）
 *   - 熔断状态徽章 + 重置按钮直连后端
 */
import { useEffect, useState } from 'react';
import { ScoringWeightsEditor } from './ScoringWeightsEditor';
import { CircuitBreakerStatus } from './CircuitBreakerStatus';
import { DSparkMetricsCard } from './DSparkMetricsCard';
import { ManualConfirmationPanel } from './ManualConfirmationPanel';
import { useRouterStore } from '@/store/routerStore';
import { ipc } from '@/ipc/invoke';

export function RouterDashboard(): JSX.Element {
  const budget = useRouterStore((s) => s.budget);
  const runMode = useRouterStore((s) => s.runMode);
  const setRunMode = useRouterStore((s) => s.setRunMode);
  const sparkEnabled = useRouterStore((s) => s.sparkEnabled);
  const setSparkEnabled = useRouterStore((s) => s.setSparkEnabled);

  // V2 增量：从后端拉真 metrics（5 秒轮询）
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const refresh = async (): Promise<void> => {
      setRefreshing(true);
      try {
        const m = await ipc.routerGetMetrics();
        if (cancelled) return;
        useRouterStore.getState().setMetrics({
          circuits: m.circuits,
          budget: m.budget,
          backends: m.backends,
        });
        setMetricsError(null);
      } catch (e) {
        if (!cancelled) setMetricsError(String(e));
      } finally {
        if (!cancelled) setRefreshing(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  // V2 增量：Spark toggle 调后端（替换纯前端 store）
  const handleSparkToggle = async (checked: boolean): Promise<void> => {
    setSparkEnabled(checked); // 乐观更新
    try {
      await ipc.routerSetSparkMode(checked);
    } catch (e) {
      // 回滚
      setSparkEnabled(!checked);
      // eslint-disable-next-line no-console
      console.error('[RouterDashboard] spark toggle failed:', e);
    }
  };

  const usedPct = budget.daily_limit > 0 ? (budget.daily_spent / budget.daily_limit) * 100 : 0;
  const barColor = usedPct > 80 ? '#cd3131' : usedPct > 50 ? '#795e26' : '#059669';

  return (
    <div className="flex h-full flex-col gap-4 overflow-auto p-4" style={{ backgroundColor: '#ffffff' }}>
      <h2 className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
        🧭 路由仪表盘
      </h2>

      {metricsError && (
        <div
          className="rounded px-2 py-1 text-2xs"
          style={{ backgroundColor: '#3c1e1e', color: '#cd3131' }}
        >
          ⚠ 指标拉取失败 · {metricsError}（{refreshing ? '重试中' : '已停止'}）
        </div>
      )}

      {/* 模式切换 + Spark 模式 */}
      <div
        className="rounded border p-4"
        style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4' }}
      >
        <h3 className="mb-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          ⚙️ 执行模式
        </h3>
        <p className="mb-3 text-2xs" style={{ color: '#616161' }}>
          自动 = 按路由结果直接执行；手动 = 弹出候选项让用户确认
        </p>
        <div className="mb-3 flex gap-2">
          {(['auto', 'manual'] as const).map((m) => {
            const active = runMode === m;
            return (
              <button
                key={m}
                type="button"
                onClick={() => setRunMode(m)}
                className="rounded px-3 py-1 text-ui"
                style={{
                  backgroundColor: active ? '#0e639c' : '#ececec',
                  color: active ? '#ffffff' : '#1f1f1f',
                }}
              >
                {m === 'auto' ? '▶ 自动' : '⏸ 手动'}
              </button>
            );
          })}
        </div>

        {/* 手动模式确认面板（5s 刷新路由决策） */}
        <ManualConfirmationPanel />

        <div
          className="flex items-center justify-between rounded px-3 py-2"
          style={{ backgroundColor: '#ffffff' }}
        >
          <div>
            <div className="text-2xs font-semibold" style={{ color: sparkEnabled ? '#795e26' : '#616161' }}>
              ⚡ Spark 模式 {sparkEnabled ? '开' : '关'}
            </div>
            <div className="text-2xs" style={{ color: '#616161' }}>
              推理模型先打草稿 → 复杂模型继续完善
            </div>
          </div>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={sparkEnabled}
              onChange={(e) => void handleSparkToggle(e.target.checked)}
              className="cursor-pointer"
            />
            <span className="text-2xs" style={{ color: '#1f1f1f' }}>
              {sparkEnabled ? '启用' : '停用'}
            </span>
          </label>
        </div>
      </div>

      {/* 预算块 */}
      <div className="rounded border p-4" style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4' }}>
        <h3 className="mb-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          💰 日预算
        </h3>
        <div className="mb-2 flex items-center justify-between text-2xs">
          <span style={{ color: '#616161' }}>
            {budget.daily_spent.toFixed(2)} / {budget.daily_limit.toFixed(2)} 元
          </span>
          <span style={{ color: barColor }}>{usedPct.toFixed(0)}%</span>
        </div>
        <div className="h-2 rounded" style={{ backgroundColor: '#ffffff' }}>
          <div
            className="h-2 rounded"
            style={{ width: `${Math.min(usedPct, 100)}%`, backgroundColor: barColor }}
          />
        </div>
        {refreshing && (
          <div className="mt-1 text-2xs" style={{ color: '#616161' }}>
            ⟳ 拉取中…
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ScoringWeightsEditor />
        <CircuitBreakerStatus />
      </div>

      {/* Phase 13 V0: DSpark 推测解码指标卡 */}
      <DSparkMetricsCard />
    </div>
  );
}