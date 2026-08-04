/**
 * SkillCard —— 单个 skill 卡片。
 */
import type { Skill } from '@/types/skill';

const RISK_ICON = { high: '🔴', medium: '🟡', low: '🟢' } as const;
const RISK_COLOR = { high: '#cd3131', medium: '#795e26', low: '#059669' } as const;

interface SkillCardProps {
  skill: Skill;
  selected: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onToggleEnabled: () => void;
}

export function SkillCard({
  skill,
  selected,
  onSelect,
  onEdit,
  onDelete,
  onToggleEnabled,
}: SkillCardProps): JSX.Element {
  return (
    <div
      className="rounded border p-3 transition-colors"
      style={{
        backgroundColor: selected ? '#0e639c' : '#f3f3f3',
        borderColor: selected ? '#007acc' : '#1f1f1f',
      }}
    >
      <div className="mb-2 flex items-start gap-2">
        <span style={{ color: RISK_COLOR[skill.risk_level], fontSize: 14 }}>
          {RISK_ICON[skill.risk_level]}
        </span>
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={onSelect}
            className="text-ui font-semibold text-left"
            style={{ color: '#1f1f1f' }}
          >
            {skill.name}
          </button>
          <div className="text-2xs" style={{ color: '#616161' }}>
            {skill.id} · v{skill.version}
          </div>
        </div>
        <label className="flex items-center gap-1 text-2xs" style={{ color: '#616161' }}>
          <input
            type="checkbox"
            checked={skill.enabled}
            onChange={onToggleEnabled}
            className="cursor-pointer"
          />
          启用
        </label>
      </div>

      <p className="mb-2 text-2xs" style={{ color: '#a0a0a0', lineHeight: 1.4 }}>
        {skill.description}
      </p>

      <div className="mb-2 flex flex-wrap gap-1">
        {skill.trigger_keywords.slice(0, 5).map((kw, i) => (
          <span
            key={i}
            className="rounded px-1.5 py-0.5 text-2xs"
            style={{ backgroundColor: '#ececec', color: '#0b6bcb' }}
          >
            {kw}
          </span>
        ))}
      </div>

      <div className="flex items-center gap-2 text-2xs" style={{ color: '#616161' }}>
        <span>{skill.mcp_servers.length} servers</span>
        <span>·</span>
        <span>{skill.allowed_tools.length} tools</span>
        <div className="ml-auto flex gap-1">
          <button
            type="button"
            onClick={onEdit}
            className="rounded px-2 py-0.5 transition-colors hover:bg-vscode-border"
            style={{ color: '#0451a5' }}
          >
            ✏️ 编辑
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded px-2 py-0.5 transition-colors hover:bg-vscode-border"
            style={{ color: '#cd3131' }}
          >
            🗑 删除
          </button>
        </div>
      </div>
    </div>
  );
}
