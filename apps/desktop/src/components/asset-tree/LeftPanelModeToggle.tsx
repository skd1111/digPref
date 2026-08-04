/**
 * LeftPanelModeToggle —— Phase 2G V1.3 📁/🧩 切换按钮。
 *
 * 位置：SideBar 顶部（紧贴标题）。
 * 行为：三态循环 'auto' → 'system' → 'business' → 'auto'。
 * 持久化：useUIStore.persist 写到 localStorage 'eaide.ui'。
 *
 * V1.3 仅做最小可用：纯文本 emoji 按钮（📁 = 系统资产，🧩 = 业务功能点，⚙ = auto）。
 * V1.5 接全局 Tooltip + i18n。
 */
import { useUIStore } from '@/store/uiStore';

type Mode = 'auto' | 'system' | 'business';

const NEXT: Record<Mode, Mode> = {
  auto: 'system',
  system: 'business',
  business: 'auto',
};

const ICON: Record<Mode, string> = {
  auto: '⚙',
  system: '📁',
  business: '🧩',
};

const LABEL: Record<Mode, string> = {
  auto: 'auto (按 WorkMode 自动)',
  system: '系统资产树',
  business: '业务功能点',
};

export function LeftPanelModeToggle(): JSX.Element {
  const mode = useUIStore((s) => s.leftPanelMode);
  const setLeftPanelMode = useUIStore((s) => s.setLeftPanelMode);

  const handleClick = () => {
    setLeftPanelMode(NEXT[mode]);
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      title={`左侧栏内容：${LABEL[mode]}（点击切换：auto / 系统资产 / 业务功能点）`}
      aria-label={`左侧栏模式：${LABEL[mode]}`}
      data-testid="left-panel-mode-toggle"
      className="text-[11px] px-1.5 py-0.5 rounded hover:bg-gray-200 transition-colors"
    >
      <span aria-hidden="true">{ICON[mode]}</span>
      <span className="ml-1 text-gray-600">{LABEL[mode]}</span>
    </button>
  );
}