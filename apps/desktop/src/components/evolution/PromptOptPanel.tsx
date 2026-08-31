/**
 * PromptOptPanel —— Few-shot 影子优化实验面板（Phase 19 V1.5 自进化 L3）。
 *
 * 针对表现不佳的技能运行影子优化实验：离线回放历史请求，比较新旧
 * few-shot 的 Judge 评分；仅显著增益产出候选版本，采纳/回滚由人工决定
 * （设计文档 §5；影子评测不影响在线链路）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { ipc } from '@/ipc/invoke';
import { EVT } from '@/ipc/events';
import { useSkillsStore } from '@/store/skillsStore';

type PromptVersion = {
  id: number;
  skill_id: string;
  version: number;
  few_shot: Array<{ role: string; content: string }>;
  gain: number | null;
  status: 'candidate' | 'active' | 'rolled_back';
  ts: string;
};

type ExperimentResult = {
  skill_id: string;
  old_avg: number;
  new_avg: number;
  gain: number;
  significant: boolean;
  version_id: number | null;
  auto_adopted: boolean;
};

const STATUS_LABEL: Record<PromptVersion['status'], string> = {
  candidate: '待采纳',
  active: '生效中',
  rolled_back: '已回滚',
};

export function PromptOptPanel(): JSX.Element {
  const skills = useSkillsStore((s) => s.skills);
  const loadSkills = useSkillsStore((s) => s.loadSkills);
  const [skillId, setSkillId] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ExperimentResult | null>(null);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [error, setError] = useState('');
  // 事件回调里读最新 skillId（避免闭包过期）
  const skillIdRef = useRef(skillId);
  skillIdRef.current = skillId;

  const reloadVersions = useCallback(async (sid: string): Promise<void> => {
    try {
      const res = await ipc.evolutionPromptVersions(sid || undefined);
      setVersions(res.items ?? []);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    void reloadVersions(skillId);
    // 实验完成（后台 / 其他入口）→ 刷新版本列表
    const un = listen(EVT.AGENT_EVOLUTION_EXPERIMENT_DONE, () => {
      void reloadVersions(skillIdRef.current);
    });
    return () => {
      void un.then((f) => f());
    };
  }, [reloadVersions, skillId]);

  const runExperiment = async (): Promise<void> => {
    if (!skillId || running) return;
    setRunning(true);
    setError('');
    setResult(null);
    try {
      const res = await ipc.evolutionPromptOptRun({ skillId });
      setResult(res);
      await reloadVersions(skillId);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  };

  const apply = async (id: number): Promise<void> => {
    try {
      await ipc.evolutionPromptVersionApply(id);
      await loadSkills();
      await reloadVersions(skillId);
    } catch (e) {
      setError(String(e));
    }
  };

  const rollback = async (id: number): Promise<void> => {
    try {
      await ipc.evolutionPromptVersionRollback(id);
      await loadSkills();
      await reloadVersions(skillId);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-2">
      <div>
        <h3 className="text-ui font-semibold" style={{ color: '#1f1f1f' }}>
          Few-shot 影子优化实验
        </h3>
        <p className="mt-0.5 text-[11px]" style={{ color: '#6b7280' }}>
          离线回放历史请求对比新旧示例评分；仅显著增益产出候选版本，采纳与回滚由你决定。
        </p>
      </div>

      <div className="flex items-center gap-2">
        <select
          value={skillId}
          onChange={(e) => setSkillId(e.target.value)}
          className="flex-1 rounded border px-2 py-1 text-ui outline-none"
          style={{ borderColor: '#d4d4d4', backgroundColor: '#ffffff', color: '#1f1f1f' }}
        >
          <option value="">选择技能…</option>
          {skills.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}（{s.id}）
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={!skillId || running}
          onClick={() => void runExperiment()}
          className="rounded px-3 py-1 text-ui font-semibold disabled:cursor-not-allowed disabled:opacity-40"
          style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
        >
          {running ? '实验中…' : '运行实验'}
        </button>
      </div>

      {error !== '' && (
        <div
          className="rounded border px-3 py-2 text-ui"
          style={{ borderColor: '#fca5a5', backgroundColor: '#fef2f2', color: '#dc2626' }}
        >
          {error}
        </div>
      )}

      {result !== null && (
        <div
          className="rounded border px-3 py-2 text-ui"
          style={{
            borderColor: result.significant ? '#86efac' : '#e7e5e4',
            backgroundColor: result.significant ? '#f0fdf4' : '#f9fafb',
          }}
        >
          旧版均分 {result.old_avg.toFixed(2)} → 新版均分 {result.new_avg.toFixed(2)}（增益{' '}
          {result.gain >= 0 ? '+' : ''}
          {result.gain.toFixed(2)}）
          {result.significant
            ? result.auto_adopted
              ? '：显著提升，已自动采纳'
              : '：显著提升，候选版本已生成，可在下方采纳'
            : '：提升不显著，未产出候选版本'}
        </div>
      )}

      {versions.length > 0 && (
        <ul className="space-y-1.5">
          {versions.map((v) => (
            <li
              key={v.id}
              className="flex items-center justify-between gap-2 rounded border px-3 py-1.5"
              style={{ borderColor: '#e7e5e4', backgroundColor: '#ffffff' }}
            >
              <div className="min-w-0 flex-1 text-ui" style={{ color: '#1f1f1f' }}>
                <span className="font-mono text-[11px]">{v.skill_id}</span> v{v.version}
                <span className="ml-2 text-[10px]" style={{ color: '#9ca3af' }}>
                  {STATUS_LABEL[v.status]}
                  {v.gain !== null && ` · 增益 ${v.gain >= 0 ? '+' : ''}${v.gain.toFixed(2)}`}
                  {` · ${v.few_shot.length} 条示例`}
                </span>
              </div>
              <div className="flex flex-shrink-0 items-center gap-1.5">
                {v.status === 'candidate' && (
                  <button
                    type="button"
                    onClick={() => void apply(v.id)}
                    className="rounded px-2 py-0.5 text-[11px] font-semibold"
                    style={{ backgroundColor: '#0e639c', color: '#ffffff' }}
                  >
                    采纳
                  </button>
                )}
                {v.status === 'active' && (
                  <button
                    type="button"
                    onClick={() => {
                      if (confirm('确认回滚到上一版本？')) void rollback(v.id);
                    }}
                    className="rounded border px-2 py-0.5 text-[11px] transition-colors hover:bg-[#fef2f2]"
                    style={{ borderColor: '#fca5a5', color: '#dc2626', backgroundColor: '#ffffff' }}
                  >
                    回滚
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
