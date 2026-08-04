/**
 * SkillRoutingBadge —— Phase 2D 当前激活 skill 的 chip。
 * 挂载位置：CenterChatFlow ChatInput 上方（与 ContextChip 并列，Phase 2G 模式）。
 */
import { useChatStore } from '@/store/chatStore';

export function SkillRoutingBadge(): JSX.Element | null {
  const selectedSkill = useChatStore((s) => s.selectedSkill);
  const setSelectedSkill = useChatStore((s) => s.setSelectedSkill);

  if (!selectedSkill) return null;

  const handleClose = (): void => {
    setSelectedSkill(null);
  };

  return (
    <div
      className="mb-2 flex items-center gap-2 rounded px-2 py-1 text-2xs"
      style={{
        backgroundColor: '#c586c020',
        border: '1px solid #c586c0',
        color: '#c586c0',
      }}
      title={`命中关键词：${selectedSkill.matched_keywords.join(', ')}`}
    >
      <span>🧠</span>
      <span className="flex-1 truncate">
        当前技能：<strong style={{ color: '#1f1f1f' }}>{selectedSkill.skill_name}</strong>
      </span>
      <button
        type="button"
        onClick={handleClose}
        className="flex-shrink-0 rounded px-1 transition-colors hover:bg-vscode-border"
        style={{ color: '#616161' }}
        title="关闭技能提示"
        aria-label="关闭技能提示"
      >
        ✕
      </button>
    </div>
  );
}
