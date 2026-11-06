/**
 * SettingsView — VSCode 风格设置页。
 *
 * 布局：
 *   - 顶栏：搜索框（VSCode 顶部 + 过滤）
 *   - 左侧：分类导航（Model / 凭证 / 终端 / 关于）
 *   - 右侧：当前分类的设置项
 *
 * 路由：
 *   /settings          → 默认进模型管理
 *   /settings/models   → 模型管理（多后端注册表 + 路由表）
 *   /settings/secrets  → 凭证保险箱
 *   /settings/terminal → 终端
 *   /settings/about    → 关于
 */
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ModelManagementPanel } from './settings/ModelManagementPanel';
import { GenLimitsPanel } from './settings/GenLimitsPanel';
import { DSparkSettingsPanel } from './settings/DSparkSettingsPanel';
import { SecretsSettingPanel } from './settings/SecretsSettingPanel';
import { TerminalSettingPanel } from './settings/TerminalSettingPanel';
import { AboutSettingPanel } from './settings/AboutSettingPanel';
import { EnvironmentsSettingPanel } from './settings/EnvironmentsSettingPanel';
import { SkillsManager } from '@/components/skills/SkillsManager';
import { ExpertTeamsPanel } from '@/components/expert-teams/ExpertTeamsPanel';
import { RouterDashboard } from '@/components/router/RouterDashboard';
import { CodeNavSettingsPanel } from '@/components/codenav/CodeNavSettingsPanel';
import { ToolchainSettingsPanel } from './settings/ToolchainSettingsPanel';
import { WorkspaceSettingsPanel } from './settings/WorkspaceSettingsPanel';
import { McpSettingsPanel } from './settings/McpSettingsPanel';
import { AdvancedSettingsPanel } from './settings/AdvancedSettingsPanel';
import { EvolutionPanel } from './settings/EvolutionPanel';
import { KnowledgeRagPanel } from './settings/KnowledgeRagPanel';
import {
  BookIcon,
  BotIcon,
  BracesIcon,
  CloseIcon,
  CompassIcon,
  ComponentIcon,
  FolderIcon,
  GlobeIcon,
  InfoCircleIcon,
  KeyIcon,
  MessageIcon,
  PlugIcon,
  SettingsIcon,
  SproutIcon,
  TerminalIcon,
  UsersIcon,
  WrenchIcon,
  ZapIcon,
  type IconComponent,
} from '@/components/ui/Icons';

type SectionId = 'envs' | 'models' | 'gen-limits' | 'dspark' | 'secrets' | 'terminal' | 'about' | 'skills' | 'expert-teams' | 'router' | 'codenav' | 'toolchain' | 'workspace' | 'advanced' | 'mcp' | 'evolution' | 'knowledge';

const SECTIONS: { id: SectionId; label: string; icon: IconComponent }[] = [
  { id: 'envs', label: 'Environments', icon: GlobeIcon },
  { id: 'mcp', label: 'MCP', icon: PlugIcon },
  { id: 'workspace', label: '工作空间', icon: FolderIcon },
  { id: 'models', label: '模型管理', icon: BotIcon },
  { id: 'gen-limits', label: '模型与回复', icon: MessageIcon },
  { id: 'dspark', label: 'DSpark 推测解码', icon: ZapIcon },
  { id: 'secrets', label: 'Secrets', icon: KeyIcon },
  { id: 'terminal', label: 'Terminal', icon: TerminalIcon },
  { id: 'about', label: 'About', icon: InfoCircleIcon },
  { id: 'skills', label: '技能', icon: ComponentIcon },
  { id: 'evolution', label: '经验库（自进化）', icon: SproutIcon },
  { id: 'expert-teams', label: '专家团', icon: UsersIcon },
  { id: 'router', label: '路由仪表盘', icon: CompassIcon },
  { id: 'codenav', label: '代码导航', icon: BracesIcon },
  { id: 'knowledge', label: '知识库/RAG', icon: BookIcon },  // 审核专家 + 聊天共用的本地混合检索
  { id: 'toolchain', label: '工具链与编译', icon: WrenchIcon },  // Phase 18；2026-08-28 并入原「编译配置」
  { id: 'advanced', label: '高级设置', icon: SettingsIcon },  // 推理模式 + 会话自主性（2026-08-05）
];

const KNOWN_SEGS: SectionId[] = ['envs', 'models', 'gen-limits', 'dspark', 'secrets', 'terminal', 'about', 'skills', 'expert-teams', 'router', 'codenav', 'toolchain', 'workspace', 'advanced', 'mcp', 'evolution', 'knowledge'];

export function SettingsView(): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();

  const initial: SectionId = (() => {
    const seg = location.pathname.replace('/settings', '').replace(/^\//, '');
    if ((KNOWN_SEGS as string[]).includes(seg)) return seg as SectionId;
    return 'models';
  })();

  const [active, setActive] = useState<SectionId>(initial);
  const [query, setQuery] = useState('');

  useEffect(() => {
    setActive((() => {
      const seg = location.pathname.replace('/settings', '').replace(/^\//, '');
      if ((KNOWN_SEGS as string[]).includes(seg)) return seg as SectionId;
      return 'models';
    })());
  }, [location.pathname]);

  const switchTo = (id: SectionId): void => {
    setActive(id);
    navigate(id === 'models' ? '/settings' : `/settings/${id}`);
  };

  // Esc 关闭 Settings 回主页
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') navigate('/');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate]);

  return (
    <div
      className="flex h-full flex-col"
      style={{ backgroundColor: '#ffffff', color: '#1f1f1f' }}
    >
      {/* 顶部工具条：标题 + 关闭按钮 */}
      <div
        className="flex h-[35px] flex-shrink-0 items-center justify-between border-b px-4"
        style={{ backgroundColor: '#ececec', borderColor: '#d0d0d0' }}
      >
        <span className="text-ui font-semibold uppercase tracking-wider text-fg">
          Settings
        </span>
        <button
          type="button"
          onClick={() => navigate('/')}
          title="关闭并返回主页 (Esc)"
          className="btn btn-ghost btn-icon"
          aria-label="关闭"
        >
          <CloseIcon size={13} />
        </button>
      </div>

      {/* 主体：左导航 + 右内容 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 左导航 */}
        <aside
          className="flex w-[220px] flex-col border-r p-2"
          style={{ backgroundColor: '#f3f3f3', borderColor: '#e0e0e0' }}
        >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索设置…"
          className="mb-3 rounded px-2 py-1 text-ui outline-none placeholder:text-fg-muted"
          style={{
            backgroundColor: '#ececec',
            color: '#1f1f1f',
            border: '1px solid #d4d4d4',
          }}
        />
        <nav className="flex-1 space-y-0.5">
          {SECTIONS.map((s) => {
            const Icon = s.icon;
            return (
            <button
              key={s.id}
              type="button"
              onClick={() => switchTo(s.id)}
              className={`nav-item ${active === s.id ? 'nav-item-active' : ''}`}
            >
              <span className={active === s.id ? '' : 'text-fg-muted'} aria-hidden="true">
                <Icon size={15} />
              </span>
              <span>{s.label}</span>
            </button>
            );
          })}
        </nav>
      </aside>

        {/* 右内容 */}
        <main className="flex-1 overflow-auto p-6">
          {active === 'envs' && <EnvironmentsSettingPanel />}
          {active === 'mcp' && <McpSettingsPanel />}
          {active === 'models' && <ModelManagementPanel />}
          {active === 'gen-limits' && <GenLimitsPanel />}
          {active === 'dspark' && <DSparkSettingsPanel />}
          {active === 'secrets' && <SecretsSettingPanel />}
          {active === 'terminal' && <TerminalSettingPanel />}
          {active === 'about' && <AboutSettingPanel />}
          {active === 'skills' && <SkillsManager />}
          {active === 'evolution' && <EvolutionPanel />}
          {active === 'expert-teams' && <ExpertTeamsPanel />}
          {active === 'router' && <RouterDashboard />}
          {active === 'codenav' && <CodeNavSettingsPanel />}
          {active === 'toolchain' && <ToolchainSettingsPanel />}
          {active === 'workspace' && <WorkspaceSettingsPanel />}
          {active === 'advanced' && <AdvancedSettingsPanel />}
          {active === 'knowledge' && <KnowledgeRagPanel />}
        </main>
      </div>
    </div>
  );
}
