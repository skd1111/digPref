/**
 * assetTypeIcons —— 系统资产类型 → 单色 SVG 图标映射（2026-11-04）。
 *
 * 这张表此前在 SystemAssetTree / FindInFiles / QuickOpen / AssetConfigDialog
 * 四处各写一份 emoji 字面量（🗄 🌐 🔐 🤖），改图标要改四处。
 * 现在收敛到本文件，各处 import 复用。
 *
 * 颜色：图标走 currentColor，随外层容器的 color 自动变化（选中/hover 都跟着变）。
 */
import { BotIcon, DatabaseIcon, GlobeIcon, KeyIcon, type IconComponent } from '@/components/ui/Icons';
import type { AssetNode } from '@/store/assetStore';

export const ASSET_TYPE_ICONS: Record<AssetNode['type'], IconComponent> = {
  database: DatabaseIcon,
  rest: GlobeIcon,
  ssh: KeyIcon,
  rpa: BotIcon,
};
