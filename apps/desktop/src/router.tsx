/**
 * React Router config — currently two routes:
 *   /                         -> Workspace (four-quadrant IDE)
 *   /settings /settings/models -> Settings (默认模型管理)
 *   /settings/secrets         -> 凭证
 *   /settings/terminal        -> 终端
 *   /settings/about           -> 关于
 *   /settings/skills          -> 技能
 *   /settings/codenav         -> 代码导航
 *   /settings/advanced        -> 高级设置（推理模式 + 会话自主性）
 */
import { createBrowserRouter } from 'react-router-dom';
import { WorkspaceLayout } from './layouts/WorkspaceLayout';
import { HomeView } from './views/HomeView';
import { SettingsView } from './views/SettingsView';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <WorkspaceLayout />,
    children: [
      { index: true, element: <HomeView /> },
      { path: 'settings', element: <SettingsView /> },
      { path: 'settings/envs', element: <SettingsView /> },
      { path: 'settings/models', element: <SettingsView /> },
      { path: 'settings/secrets', element: <SettingsView /> },
      { path: 'settings/terminal', element: <SettingsView /> },
      { path: 'settings/about', element: <SettingsView /> },
      { path: 'settings/skills', element: <SettingsView /> },
      { path: 'settings/expert-teams', element: <SettingsView /> },
      { path: 'settings/dspark', element: <SettingsView /> },
      { path: 'settings/router', element: <SettingsView /> },
      { path: 'settings/codenav', element: <SettingsView /> },
      { path: 'settings/toolchain', element: <SettingsView /> },  // Phase 18
      { path: 'settings/advanced', element: <SettingsView /> },  // 高级设置（2026-08-05）
    ],
  },
]);