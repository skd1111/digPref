/**
 * SubAgentPanel —— 多智能体派生树面板（Phase 12 V2 自动模式）。
 *
 * 变更：V2 起「是否启用多智能体」由 Agent 编排决策器自动判断（LLM 决策 +
 * 安全门槛），用户不再手动派生。本面板只读展示自动派生的 sub-agent 树：
 *   - 挂载时拉一次列表 + 派生树统计
 *   - 订阅 agent://sub_agent_spawn / _progress / _done 实时刷新
 * 手动派生入口（「派生 sub-agent」按钮 + 表单）已移除。
 */
import { useEffect, useState } from 'react';
import { listen, EVT } from '@/ipc/events';
import { ipc } from '@/ipc/invoke';

interface SubAgentItem {
  sub_agent_id: string;
  parent_run_id: string;
  parent_sub_agent_id: string | null;
  status: string;
  task_type: string | null;
  started_at: string | null;
  finished_at: string | null;
  latency_ms: number;
  confidence: number;
}

interface TreeStats {
  total_nodes: number;
  max_depth: number;
  max_total_nodes: number;
  headroom_depth: number;
  headroom_nodes: number;
}

const STATUS_COLOR: Record<string, string> = {
  pending: '#616161',
  running: '#795e26',
  ok: '#059669',
  err: '#cd3131',
  dlq: '#cd3131',
  cancelled: '#616161',
};

const STATUS_ICON: Record<string, string> = {
  pending: '…',
  running: '⏳',
  ok: '✓',
  err: '✗',
  dlq: '✗',
  cancelled: '⊘',
};

export function SubAgentPanel(): JSX.Element {
  const [items, setItems] = useState<SubAgentItem[]>([]);
  const [stats, setStats] = useState<TreeStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = async (silent = false): Promise<boolean> => {
    setLoading(true);
    try {
      const list = await ipc.orchestratorList();
      setItems(list.items ?? []);
      const s = await ipc.orchestratorTreeStats();
      setStats(s);
      setErr(null);
      return true;
    } catch (e) {
      if (!silent) {
        setErr(String(e));
      }
      return false;
    } finally {
      setLoading(false);
    }
  };

  // 初始加载 + 订阅 orchestrator SSE 事件实时刷新（V1 通道）
  useEffect(() => {
    // 启动时序竞态：面板挂载时 Agent（:8765）可能还没起完，
    // 首次拉取失败时自动重试（最多 10 次、间隔 3s），避免直接挂错误。
    let cancelled = false;
    const timers: Array<ReturnType<typeof setTimeout>> = [];
    const tryLoad = async (attempt: number): Promise<void> => {
      // 最后一次仍失败才展示错误横幅；之前的失败静默重试
      const ok = await refresh(attempt < 10);
      if (ok || cancelled || attempt >= 10) {
        return;
      }
      timers.push(setTimeout(() => void tryLoad(attempt + 1), 3000));
    };
    void tryLoad(0);
    const unlisteners: Array<Promise<() => void>> = [
      listen<unknown>(EVT.SUB_AGENT_SPAWN, () => {
        void refresh();
      }),
      listen<unknown>(EVT.SUB_AGENT_PROGRESS, () => {
        void refresh();
      }),
      listen<unknown>(EVT.SUB_AGENT_DONE, () => {
        void refresh();
      }),
    ];
    return () => {
      cancelled = true;
      timers.forEach((t) => clearTimeout(t));
      void Promise.all(unlisteners).then((fns) => {
        fns.forEach((fn) => {
          try {
            fn();
          } catch {
            // best-effort 卸载
          }
        });
      });
    };
  }, []);

  return (
    <div
      className="rounded p-2 text-2xs"
      style={{ backgroundColor: '#f3f3f3', color: '#1f1f1f' }}
    >
      {/* 派生树统计条（只读；由 Agent 自动派生） */}
      {stats && (
        <div className="mb-2 flex items-center gap-2" style={{ color: '#616161' }}>
          <span>
            派生 {stats.total_nodes} / {stats.max_total_nodes}
          </span>
          <span style={{ color: stats.headroom_nodes === 0 ? '#cd3131' : '#059669' }}>
            余 {stats.headroom_nodes}
          </span>
          <span>· 深度 ≤ {stats.max_depth}</span>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            className="ml-auto rounded px-1.5 py-0.5"
            style={{
              backgroundColor: '#ececec',
              color: loading ? '#616161' : '#333333',
              cursor: loading ? 'wait' : 'pointer',
            }}
          >
            {loading ? '⟳' : '刷新'}
          </button>
        </div>
      )}

      <div
        className="mb-2 rounded px-2 py-1"
        style={{ backgroundColor: '#ffffff', color: '#616161' }}
      >
        由 Agent 自动判断是否启用多智能体，无需手动派生。
      </div>

      {err && (
        <div className="my-2 rounded px-2 py-1" style={{ backgroundColor: '#3c1e1e', color: '#cd3131' }}>
          {err}
        </div>
      )}

      {items.length === 0 ? (
        <div className="py-2 text-center" style={{ color: '#616161' }}>
          暂无 sub-agent —— 当前任务由主智能体单线处理。
        </div>
      ) : (
        <ul className="space-y-1">
          {items.map((it) => (
            <li
              key={it.sub_agent_id}
              className="rounded px-2 py-1"
              style={{ backgroundColor: '#ffffff', borderLeft: `3px solid ${STATUS_COLOR[it.status] ?? '#616161'}` }}
            >
              <div className="flex items-center gap-2">
                <span style={{ color: STATUS_COLOR[it.status] }}>
                  {STATUS_ICON[it.status]}
                </span>
                <span className="font-mono" style={{ color: '#0b6bcb' }}>
                  {it.sub_agent_id.slice(0, 18)}…
                </span>
                <span style={{ color: '#616161' }}>
                  d={it.parent_sub_agent_id ? '二级' : '一级'}
                </span>
                <span style={{ color: '#616161' }}>
                  · {it.latency_ms}ms
                </span>
                <span
                  className="ml-auto rounded px-1.5 text-[10px]"
                  style={{
                    backgroundColor: STATUS_COLOR[it.status],
                    color: '#0e0e0e',
                  }}
                >
                  {it.status}
                </span>
              </div>
              <div className="mt-0.5 truncate text-[10px]" style={{ color: '#616161' }}>
                {it.task_type ?? '?'}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
