/**
 * CircuitBreakerStatus —— Phase 2C V2.0 熔断状态徽章 + 手动重置。
 *
 * V2 增量：
 *   - circuits 数据来自 useRouterStore.setMetrics()（RouterDashboard 5 秒轮询写入）
 *   - 每条记录右侧「重置」按钮调 `ipc.routerResetBreaker(name)` → 后端 Open → Closed
 *   - 失败提示（重置失败时）
 */
import { useRouterStore } from '@/store/routerStore';
import { ipc } from '@/ipc/invoke';

const CB_PILL: Record<'closed' | 'open' | 'half_open', { label: string; color: string }> = {
  closed: { label: '● 正常', color: '#059669' },
  half_open: { label: '◐ 半开', color: '#795e26' },
  open: { label: '● 熔断', color: '#cd3131' },
};

export function CircuitBreakerStatus(): JSX.Element {
  const circuits = useRouterStore((s) => s.circuits);
  const setResetBreakerPending = useRouterStore((s) => s.setResetBreakerPending);
  const resetBreakerPending = useRouterStore((s) => s.resetBreakerPending);

  const handleReset = async (name: string): Promise<void> => {
    setResetBreakerPending(name);
    try {
      await ipc.routerResetBreaker(name);
      // 成功后下次 5 秒轮询自动刷新
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error(`[CircuitBreakerStatus] reset ${name} failed:`, e);
    } finally {
      setResetBreakerPending(null);
    }
  };

  return (
    <div className="rounded border p-4" style={{ backgroundColor: '#f3f3f3', borderColor: '#d4d4d4' }}>
      <h3 className="mb-2 text-ui font-semibold" style={{ color: '#1f1f1f' }}>
        🔌 熔断器状态
      </h3>
      <p className="mb-3 text-2xs" style={{ color: '#616161' }}>
        V2: 5 秒轮询拉真状态（RouterDashboard setMetrics 写入）。Open 后点「重置」手动恢复。
      </p>

      {circuits.length === 0 ? (
        <div className="py-2 text-center text-2xs" style={{ color: '#616161' }}>
          暂无数据，等待首次轮询…
        </div>
      ) : (
        <div className="space-y-1">
          {circuits.map((c) => {
            const pill = CB_PILL[c.state];
            const pending = resetBreakerPending === c.name;
            return (
              <div
                key={c.name}
                className="flex items-center gap-2 rounded px-2 py-1 text-2xs"
                style={{ backgroundColor: '#ffffff' }}
              >
                <span className="font-mono flex-1 truncate" style={{ color: '#0b6bcb' }}>
                  {c.name}
                </span>
                <span style={{ color: pill.color }}>{pill.label}</span>
                {c.state !== 'closed' && (
                  <button
                    type="button"
                    onClick={() => void handleReset(c.name)}
                    disabled={pending}
                    className="rounded px-1.5 py-0.5 text-2xs"
                    style={{
                      backgroundColor: pending ? '#ececec' : '#0e639c',
                      color: pending ? '#616161' : '#ffffff',
                      cursor: pending ? 'wait' : 'pointer',
                    }}
                  >
                    {pending ? '…' : '重置'}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}