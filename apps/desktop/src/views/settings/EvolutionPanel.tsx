/**
 * EvolutionPanel — 设置页「经验库」面板（Phase 19 V0 自进化闭环）。
 *
 * 展示从失败反思中提炼的经验（experiences），支持人工启停 / 删除
 * （设计文档 §3.3：用户是否决者）。新经验经 SSE
 * evolution_insight_created 事件实时刷新。
 */
import { useCallback, useEffect, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { ipc } from '@/ipc/invoke';
import { EVT } from '@/ipc/events';
import { PromptOptPanel } from '@/components/evolution/PromptOptPanel';

type Experience = {
  id: number;
  insight: string;
  tags: string[];
  applies_to: string;
  attribution: string;
  hit_count: number;
  score: number;
  status: 'active' | 'disabled';
  ts: string;
};

type EvolutionStats = {
  signals_total: number;
  user_signals: number;
  user_up: number;
  env_fail: number;
  judge_avg: number | null;
  experiences_active: number;
  drafts_pending: number;
};

const ATTRIBUTION_LABEL: Record<string, string> = {
  prompt: '指令不清',
  tool: '工具问题',
  reasoning: '推理错误',
  env: '环境/数据',
  unknown: '未归因',
};

export function EvolutionPanel(): JSX.Element {
  const [items, setItems] = useState<Experience[]>([]);
  const [stats, setStats] = useState<EvolutionStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const reload = useCallback(async (): Promise<void> => {
    try {
      const [res, statsRes] = await Promise.all([
        ipc.evolutionExperiences(),
        ipc.evolutionStats().catch(() => null),
      ]);
      setItems(res.items ?? []);
      setStats(statsRes);
      setError('');
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    // 新经验产出（后台反思完成）→ 自动刷新列表
    const un = listen(EVT.AGENT_EVOLUTION_INSIGHT_CREATED, () => {
      void reload();
    });
    return () => {
      void un.then((f) => f());
    };
  }, [reload]);

  const toggle = async (id: number): Promise<void> => {
    try {
      await ipc.evolutionExperienceToggle(id);
      await reload();
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (id: number): Promise<void> => {
    try {
      await ipc.evolutionExperienceDelete(id);
      await reload();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-base font-semibold" style={{ color: '#1f1f1f' }}>
          经验库（自进化）
        </h2>
        <p className="mt-1 text-ui" style={{ color: '#6b7280' }}>
          任务失败或收到 👎 后，智能体会反思提炼经验；同类任务再次执行时自动参考。
          你可随时停用或删除任何一条经验。
        </p>
      </div>

      {error !== '' && (
        <div
          className="rounded border px-3 py-2 text-ui"
          style={{ borderColor: '#fca5a5', backgroundColor: '#fef2f2', color: '#dc2626' }}
        >
          {error}
        </div>
      )}

      {/* 进化看板（V1）：信号分布 / 经验数 / 待审草稿 / Judge 均分 */}
      {stats !== null && (
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {[
            { label: '评测信号', value: String(stats.signals_total) },
            {
              label: '用户反馈',
              value: `${stats.user_up} 👍 / ${stats.user_signals - stats.user_up} 👎`,
            },
            {
              label: 'Judge 均分',
              value: stats.judge_avg !== null ? stats.judge_avg.toFixed(2) : '—',
            },
            {
              label: '待审草稿',
              value: String(stats.drafts_pending),
            },
          ].map((card) => (
            <div
              key={card.label}
              className="rounded border px-3 py-2"
              style={{ borderColor: '#e7e5e4', backgroundColor: '#f9fafb' }}
            >
              <div className="text-[10px]" style={{ color: '#9ca3af' }}>
                {card.label}
              </div>
              <div className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
                {card.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <div className="text-ui" style={{ color: '#9ca3af' }}>
          加载中…
        </div>
      ) : items.length === 0 ? (
        <div
          className="rounded border border-dashed px-4 py-6 text-center text-ui"
          style={{ borderColor: '#d4d4d4', color: '#9ca3af' }}
        >
          暂无经验。智能体从失败与反馈中学习，经验会随使用逐步积累。
        </div>
      ) : (
        <ul className="space-y-2">
          {items.map((exp) => (
            <li
              key={exp.id}
              className="rounded border px-3 py-2"
              style={{
                borderColor: '#e7e5e4',
                backgroundColor: exp.status === 'active' ? '#ffffff' : '#f5f5f4',
                opacity: exp.status === 'active' ? 1 : 0.6,
              }}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="text-ui" style={{ color: '#1f1f1f' }}>
                    {exp.insight}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px]" style={{ color: '#9ca3af' }}>
                    {exp.tags.map((t) => (
                      <span
                        key={t}
                        className="rounded px-1 py-0.5"
                        style={{ backgroundColor: '#f3f4f6', color: '#6b7280' }}
                      >
                        {t}
                      </span>
                    ))}
                    <span>归因：{ATTRIBUTION_LABEL[exp.attribution] ?? exp.attribution}</span>
                    <span>命中 {exp.hit_count} 次</span>
                    <span>置信 {exp.score.toFixed(2)}</span>
                    {exp.applies_to !== '' && <span>适用：{exp.applies_to}</span>}
                  </div>
                </div>
                <div className="flex flex-shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => void toggle(exp.id)}
                    className="rounded border px-2 py-0.5 text-[11px] transition-colors hover:bg-[#f5f5f4]"
                    style={{ borderColor: '#d4d4d4', color: '#1f1f1f', backgroundColor: '#ffffff' }}
                  >
                    {exp.status === 'active' ? '停用' : '启用'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void remove(exp.id)}
                    className="rounded border px-2 py-0.5 text-[11px] transition-colors hover:bg-[#fef2f2]"
                    style={{ borderColor: '#fca5a5', color: '#dc2626', backgroundColor: '#ffffff' }}
                  >
                    删除
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Phase 19 V1.5：Few-shot 影子优化实验 */}
      <div className="pt-2">
        <PromptOptPanel />
      </div>
    </div>
  );
}
