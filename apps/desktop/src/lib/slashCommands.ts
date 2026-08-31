/**
 * slashCommands.ts —— 输入框 `/` 系统指令（2026-08-28，V1：Skill）。
 *
 * 交互契约：输入框文本以 `/` 开头（单行）时触发指令菜单；`/` 之后到第一个
 * 空格为止是查询词，用于过滤 Skill 列表。纯函数与组件分离，方便单测。
 */
import type { Skill } from '@/types/skill';

/** 菜单最多展示条数（多了滚动没意义，Skill 总量本身不大） */
export const SLASH_MAX_ITEMS = 12;

/**
 * 解析输入框文本中的 `/` 指令查询。
 *
 * @returns 查询词（`/` 之后、第一个空格之前）；非指令文本返 null。
 *   - 必须以 `/` 开头且不含换行（多行输入视为普通提问）
 *   - `/` 返 ''（展示全部指令）；`/off` 返 'off'；`/a b` 返 'a'
 */
export function parseSlashQuery(text: string): string | null {
  if (!text.startsWith('/') || text.includes('\n')) return null;
  const rest = text.slice(1);
  const sp = rest.search(/\s/);
  return sp === -1 ? rest : rest.slice(0, sp);
}

/**
 * 按查询词过滤可用 Skill（仅 enabled）：id / 名称 / 触发关键词 / 描述
 * 任一包含查询词即命中（不区分大小写）；空查询返全部启用项。
 * 命中优先级：id 精确 > 名称前缀 > 名称包含 > 关键词 > 描述，截断到上限。
 */
export function filterSkillsForSlash(skills: Skill[], query: string): Skill[] {
  const enabled = skills.filter((s) => s.enabled);
  const q = query.trim().toLowerCase();
  if (!q) return enabled.slice(0, SLASH_MAX_ITEMS);

  const rank = (s: Skill): number => {
    if (s.id.toLowerCase() === q) return 0;
    if (s.name.toLowerCase().startsWith(q)) return 1;
    if (s.name.toLowerCase().includes(q)) return 2;
    if ((s.trigger_keywords ?? []).some((k) => k.toLowerCase().includes(q))) return 3;
    if ((s.description ?? '').toLowerCase().includes(q)) return 4;
    return 99;
  };
  return enabled
    .map((s) => ({ s, r: rank(s) }))
    .filter((x) => x.r < 99)
    .sort((a, b) => a.r - b.r)
    .slice(0, SLASH_MAX_ITEMS)
    .map((x) => x.s);
}
