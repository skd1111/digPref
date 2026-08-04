/**
 * useAssetTree — auto-refresh the asset tree on mount.
 */
import { useEffect } from 'react';
import { useAssetStore } from '@/store/assetStore';

export function useAssetTree(): void {
  const refresh = useAssetStore((s) => s.refresh);
  useEffect(() => {
    void refresh();
  }, [refresh]);
}