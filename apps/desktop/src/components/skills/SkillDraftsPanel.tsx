/**
 * SkillDraftsPanel —— 技能页「待审草稿」区（Phase 19 V1 自进化 L2）。
 *
 * 智能体从同类任务的多次成功中蒸馏出技能草稿（规则类 YAML），
 * 默认不启用；人工审核后「采纳」才写入 skills/ 并生效（设计文档 §4）。
 * 新草稿经 SSE skill_draft_ready 事件实时刷新。
 */
import { useCallback, useEffect, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { ipc } from '@/ipc/invoke';
import { EVT } from '@/ipc/events';
import { useSkillsStore } from '@/store/skillsStore';

type SkillDraft = {
  id: number;
  slug: string;
  name: string;
  yaml_text: string;
  task_signature: string;
  status: string;
  ts: string;
};

export function SkillDraftsPanel(): JSX.Element {
  const [drafts, setDrafts] = useState<SkillDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const loadSkills = useSkillsStore((s) => s.loadSkills);

  const reload = useCallback(async (): Promise<void> => {
    try {
      const res = await ipc.evolutionSkillDrafts();
      setDrafts(res.items ?? []);
      setError('');
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    const un = listen(EVT.AGENT_SKILL_DRAFT_READY, () => {
      void reload();
    });
    return () => {
      void un.then((f) => f());
    };
  }, [reload]);

  const approve = async (id: number): Promise<void> => {
    try {
      await ipc.evolutionSkillDraftApprove(id);
      await loadSkills(); // 技能列表立即可见
      await reload();
    } catch (e) {
      setError(String(e));
    }
  };

  const reject = async (id: number): Promise<void> => {
    try {
      await ipc.evolutionSkillDraftReject(id);
      await reload();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-2">
      <p className="text-2xs" style={{ color: '#616161' }}>
        智能体从同类任务的多次成功中自动蒸馏技能草稿。草稿默认不启用，请审阅
        YAML 后决定采纳或拒绝。
      </p>

      {error !== '' && (
        <div
          className="rounded border px-3 py-2 text-2xs"
          style={{ borderColor: '#fca5a5', backgroundColor: '#fef2f2', color: '#dc2626' }}
        >
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-2xs" style={{ color: '#9ca3af' }}>
          加载中…
        </div>
      ) : drafts.length === 0 ? (
        <div
          className="rounded border border-dashed px-4 py-6 text-center text-2xs"
          style={{ borderColor: '#d4d4d4', color: '#9ca3af' }}
        >
          暂无待审草稿。同类任务多次成功后，智能体会自动蒸馏技能草稿。
        </div>
      ) : (
        <ul className="space-y-2">
          {drafts.map((d) => (
            <li
              key={d.id}
              className="rounded border px-3 py-2"
              style={{ borderColor: '#e7e5e4', backgroundColor: '#ffffff' }}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
                    🌱 {d.name}
                    <span className="ml-2 font-mono text-2xs" style={{ color: '#9ca3af' }}>
                      {d.slug}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[10px]" style={{ color: '#9ca3af' }}>
                    {d.ts} · 签名 {d.task_signature.slice(0, 12)}
                  </div>
                </div>
                <div className="flex flex-shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => setExpandedId(expandedId === d.id ? null : d.id)}
                    className="rounded border px-2 py-0.5 text-[11px] transition-colors hover:bg-[#f5f5f4]"
                    style={{ borderColor: '#d4d4d4', color: '#1f1f1f', backgroundColor: '#ffffff' }}
                  >
                    {expandedId === d.id ? '收起' : '查看'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (confirm(`确认采纳草稿「${d.name}」？采纳后将写入技能库并启用。`)) {
                        void approve(d.id);
                      }
                    }}
                    className="rounded px-2 py-0.5 text-[11px] font-semibold"
                    style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
                  >
                    采纳
                  </button>
                  <button
                    type="button"
                    onClick={() => void reject(d.id)}
                    className="rounded border px-2 py-0.5 text-[11px] transition-colors hover:bg-[#fef2f2]"
                    style={{ borderColor: '#fca5a5', color: '#dc2626', backgroundColor: '#ffffff' }}
                  >
                    拒绝
                  </button>
                </div>
              </div>
              {expandedId === d.id && (
                <pre
                  className="mt-2 max-h-64 overflow-auto rounded border p-2 font-mono text-[10px]"
                  style={{ borderColor: '#e7e5e4', backgroundColor: '#f9fafb', color: '#374151' }}
                >
                  {d.yaml_text}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 待审草稿数（SkillsManager Tab 徽标用；失败返 0）。 */
export function usePendingDraftCount(): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let alive = true;
    const fetchCount = (): void => {
      ipc
        .evolutionSkillDrafts()
        .then((res) => {
          if (alive) setCount((res.items ?? []).length);
        })
        .catch(() => undefined);
    };
    fetchCount();
    const un = listen(EVT.AGENT_SKILL_DRAFT_READY, fetchCount);
    return () => {
      alive = false;
      void un.then((f) => f());
    };
  }, []);
  return count;
}
