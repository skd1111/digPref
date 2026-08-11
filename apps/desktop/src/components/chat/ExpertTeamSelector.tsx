/**
 * ExpertTeamSelector —— ChatInput 左侧专家团选择器。
 *
 * 仅运营模式（mode === 'operator'）显示，其余模式返回 null
 * （零占位，不干扰开发/审核/数据模式）。
 * 注：不依赖 activityId —— 切 mode 时 uiStore 会重置 activityId，
 * 按活动栏判断会导致选择器消失，需点左侧导航才能恢复。
 *
 * 交互：
 *   - 「自动（跟随业务预设）」：clearSelection → 由 OperationsWorkbench
 *     按 Skill 预设 / AI 推荐重新填充
 *   - 选中具体专家团：selectManually → 切换业务不再自动改写，
 *     直到用户切回「自动」
 */
import { useUIStore } from '@/store/uiStore';
import { useExpertTeamStore } from '@/store/expertTeamStore';

const SOURCE_LABEL: Record<string, string> = {
  preset: '业务预设',
  llm: 'AI 推荐',
  keyword: 'AI 推荐（关键词）',
  manual: '手动选择',
};

export function ExpertTeamSelector(): JSX.Element | null {
  const mode = useUIStore((s) => s.mode);
  const teams = useExpertTeamStore((s) => s.teams);
  const selectedTeamIds = useExpertTeamStore((s) => s.selectedTeamIds);
  const selectionMode = useExpertTeamStore((s) => s.selectionMode);
  const selectionSource = useExpertTeamStore((s) => s.selectionSource);
  const recommending = useExpertTeamStore((s) => s.recommending);

  if (mode !== 'operator') return null;

  const enabledTeams = teams.filter((t) => t.enabled);
  const current = selectedTeamIds.length === 1 ? selectedTeamIds[0] : '';
  const isAuto = selectionMode === 'auto';

  const handleChange = (value: string): void => {
    const st = useExpertTeamStore.getState();
    if (value === '') {
      st.clearSelection();
    } else {
      st.selectManually([value]);
    }
  };

  const sourceHint = recommending
    ? '专家团准备中…'
    : selectedTeamIds.length === 0
      ? '未选择，可手动指定'
      : (SOURCE_LABEL[selectionSource] ?? selectionSource);

  return (
    <div
      className="flex flex-col justify-center gap-0.5 self-stretch rounded border px-1.5 py-1"
      style={{ borderColor: '#e7e5e4', backgroundColor: '#fafaf9', minWidth: 104 }}
      title="选择处理本业务的专家团（自动 = 跟随业务预设 / AI 推荐）"
    >
      <span className="text-[10px]" style={{ color: '#9ca3af' }}>
        👥 专家团
      </span>
      <select
        value={isAuto ? (current || '') : current}
        onChange={(e) => handleChange(e.target.value)}
        disabled={enabledTeams.length === 0}
        className="rounded border px-1 py-0.5 text-2xs outline-none"
        style={{ backgroundColor: '#ffffff', borderColor: '#d4d4d4', color: '#1f1f1f' }}
      >
        <option value="">自动（跟随业务预设）</option>
        {enabledTeams.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
      <span className="text-[10px]" style={{ color: isAuto ? '#059669' : '#b45309' }}>
        {isAuto ? sourceHint : `手动 · ${selectedTeamIds.length} 个团`}
      </span>
    </div>
  );
}
