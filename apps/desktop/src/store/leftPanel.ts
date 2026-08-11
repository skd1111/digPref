/**
 * useLeftPanelContent —— Phase 2G V1.3 左侧 SideBar 内容选择 selector。
 *
 * Phase 2H 起由 devPanelMode 接管：
 *   - 'assets'   —— 系统资产树（SystemAssetTree，开发视角）
 *   - 'files'    —— 文件列表（ProjectFileTree，界面与右侧思维链保持现状）
 *   - 'features' —— 系统功能点（BusinessFeatureTree，右侧切换为需求卡片）
 *
 * 返回 'system' | 'files' | 'business'，由 WorkspaceLayout.tsx 据此渲染对应组件。
 */
import { useUIStore } from '@/store/uiStore';

export type LeftPanelContent = 'system' | 'files' | 'business';

export function useLeftPanelContent(): LeftPanelContent {
  const mode = useUIStore((s) => s.mode);
  const devPanelMode = useUIStore((s) => s.devPanelMode);
  // 非开发模式不显示左侧子切换（auditor / analyst 有独立布局）
  if (mode !== 'full') return 'system';
  if (devPanelMode === 'files') return 'files';
  if (devPanelMode === 'features') return 'business';
  return 'system';
}
