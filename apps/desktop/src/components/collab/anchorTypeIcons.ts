/**
 * anchorTypeIcons —— 协作锚点类型 → 单色 SVG 图标映射（2026-11-04）。
 *
 * 锚点类型原本在 `types/collab.ts` 的 `ANCHOR_LABELS` 里用 unicode 字符表示
 * （⌨ ⊞ 🚀 ✓ ⌘ ⚡ ◉），显示时字号不一、字重不一，和其余 SVG 图标混排非常突兀。
 *
 * 这里把图标拆出来收敛到本文件：
 *   - `types/collab.ts` 只保留 label / color（纯数据，不引 React）
 *   - 组件侧用 `ANCHOR_TYPE_ICONS[anchor_type]` 取 SVG 组件
 *
 * 颜色：图标走 currentColor，随徽章容器的 color 自动变化。
 */
import {
  BracesIcon,
  CheckCircleIcon,
  DatabaseIcon,
  DotIcon,
  FileTextIcon,
  PackageIcon,
  ZapIcon,
  type IconComponent,
} from '@/components/ui/Icons';
import type { AnchorType } from '@/types/collab';

export const ANCHOR_TYPE_ICONS: Record<AnchorType, IconComponent> = {
  code_line: BracesIcon,
  sql_block: DatabaseIcon,
  deploy_task: PackageIcon,
  approval_ticket: CheckCircleIcon,
  log_segment: FileTextIcon,
  hotswap_task: ZapIcon,
  custom: DotIcon,
};
