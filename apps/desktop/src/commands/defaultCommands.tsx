/**
 * defaultCommands —— 注册 EAIDE 默认命令集。
 *
 * 在应用挂载时 import 一次即可生效（registerCommand 全局）。
 */
import { useEffect } from 'react';
import { useUIStore } from '@/store/uiStore';
import { useChatStore } from '@/store/chatStore';
import { ipc } from '@/ipc/invoke';
import { registerCommands } from './commandRegistry';
import {
  cloneFromGit,
  pickAndImportFolder,
  pickAndOpenFile,
} from '@/components/codenav/fileOps';

export function useDefaultCommands(): void {
  useEffect(() => {
    const cleanup = registerCommands([
      // —— Chat ——
      {
        id: 'chat.newTab',
        title: '对话: 新建会话',
        description: 'Chat: New Conversation',
        category: 'Chat',
        run: () => useChatStore.getState().newTab(),
      },
      {
        id: 'chat.closeTab',
        title: '对话: 关闭当前会话',
        description: 'Chat: Close Active Tab',
        category: 'Chat',
        run: () => {
          const cs = useChatStore.getState();
          cs.closeTab(cs.activeTabId);
        },
      },
      {
        id: 'chat.renameTab',
        title: '对话: 重命名当前会话',
        description: 'Chat: Rename Active Tab (双击 tab 也可)',
        category: 'Chat',
        run: () => {
          const cs = useChatStore.getState();
          const t = cs.tabs.find((x) => x.id === cs.activeTabId);
          if (!t) return;
          const next = window.prompt('新标题', t.title);
          if (next && next.trim()) cs.renameTab(t.id, next.trim());
        },
      },
      // —— View ——
      {
        id: 'view.openCheatSheet',
        title: '帮助: 键盘快捷键',
        description: 'Help: Keyboard Shortcuts',
        category: 'Help',
        run: () => useUIStore.getState().toggleCheatSheet(true),
      },
      {
        id: 'workbench.action.openSettings',
        title: '设置: 打开 Settings',
        description: 'Preferences: Open Settings',
        category: 'Preferences',
        run: () => {
          window.history.pushState({}, '', '/settings');
          window.dispatchEvent(new PopStateEvent('popstate'));
        },
      },
      // —— View: 占位 / 未来可接 ——
      {
        id: 'view.toggleActivityBar',
        title: '视图: 切换 Activity Bar',
        description: 'View: Toggle Activity Bar（占位）',
        category: 'View',
        run: () => {
          useUIStore.setState((s) => ({ ...s })); // noop 占位
          console.info('[view] toggleActivityBar 暂未实装');
        },
      },
      {
        id: 'workbench.action.openModelSettings',
        title: '设置: 模型管理',
        description: 'Settings: Model Management',
        category: 'Preferences',
        run: () => {
          window.history.pushState({}, '', '/settings/models');
          window.dispatchEvent(new PopStateEvent('popstate'));
        },
      },
      // —— Developer: 开发者工具 ——
      {
        id: 'workbench.action.toggleDevTools',
        title: '开发者: 切换开发者工具',
        description:
          'Developer: Toggle DevTools（config.yaml 的 devtools 开关）',
        category: 'Developer',
        run: () => {
          void ipc
            .openDevtools()
            .then((msg) => {
              // eslint-disable-next-line no-console
              console.info(`[devtools] ${msg}`);
            })
            .catch((err) => {
              // eslint-disable-next-line no-console
              console.warn('[devtools]', err);
            });
        },
      },
      // —— File: Phase 2F 代码导航导入 ——
      {
        id: 'workbench.action.openFile',
        title: '文件: 打开文件',
        description: 'File: Open File in Editor',
        category: 'File',
        run: () => void pickAndOpenFile(),
      },
      {
        id: 'workbench.action.openFolder',
        title: '文件: 打开文件夹导入索引',
        description: 'File: Open Folder and Index for Code Navigation',
        category: 'File',
        run: () => void pickAndImportFolder(),
      },
      {
        id: 'workbench.action.cloneFromGit',
        title: '文件: 从 Git 克隆（V1 占位）',
        description: 'File: Clone from Git (V1 placeholder)',
        category: 'File',
        run: () => void cloneFromGit(),
      },
    ]);

    return cleanup;
  }, []);
}
