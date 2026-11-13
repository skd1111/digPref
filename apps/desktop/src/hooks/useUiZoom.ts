/**
 * useUiZoom —— 把 uiStore.uiZoom 应用到 WebView 缩放（App 根部调用一次）。
 *
 * 缩放走 WebView 原生 zoom，文字 / 按钮 / 间距整体等比放大；
 * 菜单 View → Zoom In / Zoom Out / Reset Zoom 与设置页三档共用同一份状态，
 * 值持久化在 localStorage（eaide.ui），下次启动直接生效。
 */
import { useEffect } from 'react';
import { applyUiZoom } from '@/lib/uiZoom';
import { useUIStore } from '@/store/uiStore';

export function useUiZoom(): void {
  const uiZoom = useUIStore((s) => s.uiZoom);
  useEffect(() => {
    void applyUiZoom(uiZoom);
  }, [uiZoom]);
}
