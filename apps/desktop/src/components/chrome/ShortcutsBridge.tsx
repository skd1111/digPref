/**
 * ShortcutsBridge — 顶层占位组件，作用是挂载 useShortcuts / useDefaultCommands hook。
 * 必须放在一个 React 组件里，因为 hook 不能在 main.tsx 直接用。
 */
import { useEffect } from 'react';
import { useShortcuts } from '@/hooks/useShortcuts';
import { useDefaultCommands } from '@/commands/defaultCommands';
import { useCodeNavStore } from '@/store/codeNavStore';

export function ShortcutsBridge(): null {
  useShortcuts();
  useDefaultCommands();
  // Phase 2F V3：启动时从后端拉 opened_projects（Agent 路径护栏）
  useEffect(() => {
    void useCodeNavStore.getState().loadOpenedProjects();
  }, []);
  return null;
}
