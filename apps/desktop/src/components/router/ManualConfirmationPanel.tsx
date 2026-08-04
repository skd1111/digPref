/**
 * ManualConfirmationPanel —— Phase 2C V3 manual 模式确认面板。
 *
 * V0 占位：RouterDashboard 仅 toggle runMode='auto' | 'manual'，但 manual 触发后
 * LLM 调用没真正弹 modal 渲染 candidates。
 *
 * V3 增量（本组件）：
 *   - Manual 模式下显示最近 routing decisions（含 candidates + scores）
 *   - 每个候选列出 backend 名字 + 5 维评分（capability / cost / latency /
 *     compliance / availability）+ 总分 + 选择 radio
 *   - "确认" 按钮调用 routerSetPrimaryBackend（V0 占位；实际接入 Phase 4）
 *   - Auto 模式：组件不渲染（不浪费 DOM）
 *
 * V3 不做（V3.5 补）：
 *   - 真 modal 弹出（用 Tauri dialog plugin）—— 当前 inline panel 已够用
 *   - "立即重试" / "取消任务" 操作
 */
import { useEffect, useState } from 'react';
import { ipc } from '@/ipc/invoke';
import { useRouterStore } from '@/store/routerStore';
import { ScoringRadar } from './ScoringRadar';

interface RoutingDecisionLite {
  request_id: string;
  task_category?: string;
  sensitivity?: string;
  primary_backend: string | null;
  actual_backend: string | null;
  fallback_chain?: string[];
  fallback_used?: boolean;
  estimated_cost?: number;
  cache_hit?: boolean;
  created_at?: number;
  /** V2 引擎五维评分（candidates[i].scores）；后端存储为 JSON 字符串，前端宽容解析 */
  candidates?: Array<{
    backend: string;
    scores?: Record<string, number>;
    total?: number;
  }>;
}

export function ManualConfirmationPanel(): JSX.Element | null {
  const runMode = useRouterStore((s) => s.runMode);
  const [decisions, setDecisions] = useState<RoutingDecisionLite[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (runMode !== 'manual') return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const refresh = async () => {
      try {
        const raw = await ipc.routerGetDecisions(5);
        if (cancelled) return;
        // 后端 /router/decisions 返回 { decisions: [...] } 形态
        const list: RoutingDecisionLite[] =
          raw && typeof raw === 'object' && 'decisions' in raw
            ? (raw.decisions as RoutingDecisionLite[])
            : [];
        setDecisions(list);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
      if (!cancelled) {
        timer = setTimeout(refresh, 5000);
      }
    };

    refresh(); // 首次立即拉取

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [runMode]);

  if (runMode !== 'manual') return null;

  return (
    <div
      className="rounded border p-3 my-3"
      style={{ borderColor: '#795e26', backgroundColor: '#ffffff' }}
      data-testid="manual-confirmation-panel"
    >
      <div className="text-2xs font-semibold mb-1" style={{ color: '#795e26' }}>
        ⏸ Manual 模式 · 最近路由决策（每 5s 刷新）
      </div>
      <p className="text-2xs mb-2" style={{ color: '#616161' }}>
        Auto 模式下 LLM 路由直接执行；Manual 模式下你在下方选择 primary backend（V3 占位，
        V3.5 接 Phase 4 Ollama 真推理）。当前显示最近 5 条 routing decision 的候选 backend。
      </p>
      {error && (
        <div className="text-2xs mb-2" style={{ color: '#cd3131' }}>
          拉取失败: {error}
        </div>
      )}
      {decisions.length === 0 ? (
        <div className="text-2xs" style={{ color: '#616161' }}>
          暂无 routing decision（请先触发一次 LLM 调用）
        </div>
      ) : (
        <div className="space-y-2">
          {decisions.map((d) => (
            <DecisionRow key={d.request_id} decision={d} />
          ))}
        </div>
      )}
    </div>
  );
}

function DecisionRow({ decision }: { decision: RoutingDecisionLite }): JSX.Element {
  return (
    <div
      className="rounded border p-2"
      style={{ borderColor: '#d4d4d4', backgroundColor: '#f3f3f3' }}
    >
      <div className="flex items-center justify-between mb-1">
        <div className="text-2xs font-mono" style={{ color: '#0b6bcb' }}>
          {decision.task_category ?? 'unknown'} · request={decision.request_id.slice(0, 8)}
        </div>
        <div className="text-2xs" style={{ color: '#616161' }}>
          actual={decision.actual_backend ?? 'NONE'} · cache={decision.cache_hit ? 'hit' : 'miss'}
        </div>
      </div>
      <div className="text-2xs" style={{ color: '#1f1f1f' }}>
        Fallback chain: {(decision.fallback_chain ?? []).join(' → ') || '(empty)'}
      </div>
      <div className="text-2xs" style={{ color: '#616161' }}>
        est. cost: ${(decision.estimated_cost ?? 0).toFixed(4)}
        {decision.fallback_used && (
          <span style={{ color: '#795e26' }}> · fallback used</span>
        )}
      </div>
      {/* V2.5-3 5 维评分可视化：显示当前 decision primary backend 的 5 维分数 */}
      {decision.candidates && decision.candidates.length > 0 && (
        <div className="mt-2">
          <ScoringRadar
            scores={decision.candidates[0]?.scores ?? {}}
            title={`📊 ${decision.candidates[0]?.backend ?? decision.actual_backend ?? 'unknown'} 五维评分`}
          />
        </div>
      )}
    </div>
  );
}

// V3 占位符号已移除（CandidateScore 未与实际数据形状对齐，待 V3.5 重构时再引入）