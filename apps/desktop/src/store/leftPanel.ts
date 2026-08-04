/**
 * useLeftPanelContent —— Phase 2G V1.3 左侧 SideBar 内容选择 selector。
 *
 * `leftPanelMode` 三种取值：
 *   - 'auto'     —— 按 WorkMode 自动决定（'full' → 'system'，'operator' → 'business'，其他保留原逻辑）
 *   - 'system'   —— 强制 SystemAssetTree（开发视角）
 *   - 'business' —— 强制 BusinessFeatureTree（运营视角）
 *
 * 返回 'system' | 'business'，由 WorkspaceLayout.tsx 据此渲染对应组件。
 */
import { useUIStore } from '@/store/uiStore';

export type LeftPanelContent = 'system' | 'business';

export function useLeftPanelContent(): LeftPanelContent {
  const mode = useUIStore((s) => s.mode);
  const leftPanelMode = useUIStore((s) => s.leftPanelMode);
  if (leftPanelMode === 'system') return 'system';
  if (leftPanelMode === 'business') return 'business';
  // auto: 按 WorkMode 决定
  return mode === 'operator' ? 'business' : 'system';
}