/**
 * SlashMenu —— 输入框 `/` 系统指令下拉菜单（2026-08-28，V1：Skill）。
 *
 * 锚定在输入框上方浮出：列表 = 已启用 Skill（按查询词过滤，见
 * lib/slashCommands.ts）。键盘导航由 ChatInput 的 keydown 统一拦截
 * （activeIdx 从父级传入），本组件只负责渲染与鼠标交互。
 */
import type { Skill } from '@/types/skill';

interface SlashMenuProps {
  skills: Skill[];
  /** 当前键盘高亮项下标（父级维护） */
  activeIdx: number;
  onSelect: (skill: Skill) => void;
  /** 鼠标悬浮同步高亮（鼠标/键盘双通道共用同一高亮态） */
  onHover: (idx: number) => void;
}

export function SlashMenu({ skills, activeIdx, onSelect, onHover }: SlashMenuProps): JSX.Element {
  return (
    <div
      className="absolute bottom-full left-0 right-0 z-30 mb-1 overflow-hidden rounded-lg border bg-white shadow-lg"
      style={{ borderColor: '#e7e5e4' }}
      role="listbox"
      aria-label="系统指令"
    >
      <div
        className="flex items-center justify-between border-b px-2.5 py-1.5 text-[10px]"
        style={{ borderColor: '#f0efed', color: '#9ca3af' }}
      >
        <span>系统指令 · 业务技能（Skill）</span>
        <span>↑↓ 选择 · Enter 确认 · Esc 关闭</span>
      </div>
      <div className="max-h-64 overflow-auto py-1">
        {skills.length === 0 && (
          <div className="px-3 py-2 text-[11px]" style={{ color: '#9ca3af' }}>
            没有匹配的已启用 Skill（设置 → Skill 管理 可维护）
          </div>
        )}
        {skills.map((s, i) => (
          <button
            key={s.id}
            type="button"
            role="option"
            aria-selected={i === activeIdx}
            onClick={() => onSelect(s)}
            onMouseEnter={() => onHover(i)}
            className="flex w-full items-baseline gap-2 px-2.5 py-1.5 text-left"
            style={{
              backgroundColor: i === activeIdx ? '#f0fdf4' : 'transparent',
              color: '#1f2937',
            }}
          >
            <span className="flex-shrink-0 font-mono text-[11px]" style={{ color: '#10a37f' }}>
              /{s.id}
            </span>
            <span className="flex-shrink-0 text-[11px] font-semibold">{s.name}</span>
            {s.description && (
              <span className="min-w-0 flex-1 truncate text-[10px]" style={{ color: '#9ca3af' }}>
                {s.description}
              </span>
            )}
            <span
              className="flex-shrink-0 rounded px-1 text-[9px]"
              style={{ backgroundColor: '#f5f5f4', color: '#6b7280' }}
            >
              Skill
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
