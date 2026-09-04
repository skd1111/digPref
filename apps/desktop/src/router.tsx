/**
 * React Router config — currently two routes:
 *   /                         -> Workspace (four-quadrant IDE)
 *   /settings /settings/models -> Settings (默认模型管理)
 *   /settings/mcp               -> MCP 服务器配置（联网搜索等外部工具接入）
 *   /settings/gen-limits        -> 模型与回复（生成参数）
 *   /settings/secrets         -> 凭证
 *   /settings/terminal        -> 终端
 *   /settings/about           -> 关于
 *   /settings/skills          -> 技能
 *   /settings/evolution       -> 经验库（自进化）
 *   /settings/codenav         -> 代码导航
 *   /settings/workspace       -> 工作空间（默认安装目录/workspace，可自定义）
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
      { path: 'settings/mcp', element: <SettingsView /> },  // MCP 服务器配置
      { path: 'settings/models', element: <SettingsView /> },
      { path: 'settings/gen-limits', element: <SettingsView /> },
      { path: 'settings/secrets', element: <SettingsView /> },
      { path: 'settings/terminal', element: <SettingsView /> },
      { path: 'settings/about', element: <SettingsView /> },
      { path: 'settings/skills', element: <SettingsView /> },
      { path: 'settings/evolution', element: <SettingsView /> },  // Phase 19：经验库（自进化）
      { path: 'settings/expert-teams', element: <SettingsView /> },
      { path: 'settings/dspark', element: <SettingsView /> },
      { path: 'settings/router', element: <SettingsView /> },
      { path: 'settings/codenav', element: <SettingsView /> },
      { path: 'settings/knowledge', element: <SettingsView /> },  // 知识库/RAG（审核专家 + 聊天共用混合检索）
      { path: 'settings/toolchain', element: <SettingsView /> },  // Phase 18；2026-08-28 并入原「编译配置」
      { path: 'settings/workspace', element: <SettingsView /> },  // 工作空间路径（下方 advanced 紧跟）
      { path: 'settings/advanced', element: <SettingsView /> },  // 高级设置（2026-08-05）
    ],
  },
]);