/**
 * LeftPanelModeToggle —— Phase 2G V1.3 📁/🧩 切换按钮。
 *
 * 位置：SideBar 顶部（紧贴标题）。
 * 行为：三态循环 'auto' → 'system' → 'business' → 'auto'。
 * 持久化：useUIStore.persist 写到 localStorage 'eaide.ui'。
 *
 * V1.3 仅做最小可用：单色 SVG 图标按钮（数据库 = 系统资产，组件 = 业务功能点，齿轮 = auto）。
 * V1.5 接全局 Tooltip + i18n。
 */
import { useUIStore } from '@/store/uiStore';
import {
  ComponentIcon,
  DatabaseIcon,
  SettingsIcon,
  type IconComponent,
} from '@/components/ui/Icons';

type Mode = 'auto' | 'system' | 'business';

const NEXT: Record<Mode, Mode> = {
  auto: 'system',
  system: 'business',
  business: 'auto',
};

const ICON: Record<Mode, IconComponent> = {
  auto: SettingsIcon,
  system: DatabaseIcon,
  business: ComponentIcon,
};

const LABEL: Record<Mode, string> = {
  auto: '自动',
  system: '系统资产树',
  business: '业务功能点',
};

export function LeftPanelModeToggle(): JSX.Element {
  const mode = useUIStore((s) => s.leftPanelMode);
  const setLeftPanelMode = useUIStore((s) => s.setLeftPanelMode);

  const handleClick = () => {
    setLeftPanelMode(NEXT[mode]);
  };

  const Icon = ICON[mode];

  return (
    <button
      type="button"
      onClick={handleClick}
      title={`左侧栏内容：${LABEL[mode]}（点击切换：自动 / 系统资产 / 业务功能点）`}
      aria-label={`左侧栏模式：${LABEL[mode]}`}
      data-testid="left-panel-mode-toggle"
      className="rounded p-1 leading-none transition-colors hover:bg-gray-200"
    >
      <Icon size={16} />
    </button>
  );
}