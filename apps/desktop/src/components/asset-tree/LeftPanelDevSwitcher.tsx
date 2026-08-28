/**
 * LeftPanelDevSwitcher —— Phase 2H 开发模式左侧栏三态子切换。
 *
 * 系统资产 / 文件列表 / 系统功能点（用户要求开发模式可在这三者间切换）：
 *   - 文件列表：界面不变，右侧仍为思维链
 *   - 系统功能点：支持搜索，右侧切换为需求卡片
 */
import { useUIStore, type DevPanelMode } from "@/store/uiStore";

const OPTIONS: Array<{
  id: DevPanelMode;
  label: string;
  icon: string;
  hint: string;
}> = [
  {
    id: "assets",
    label: "系统资产",
    icon: "🗄️",
    hint: "DB / API / SSH / RPA 资产树",
  },
  {
    id: "files",
    label: "文件列表",
    icon: "📁",
    hint: "工程目录树（界面不变，右侧执行过程）",
  },
  {
    id: "features",
    label: "系统功能点",
    icon: "🧩",
    hint: "工程 AI 提炼的功能点（右侧需求卡片）",
  },
];

export function LeftPanelDevSwitcher(): JSX.Element {
  const mode = useUIStore((s) => s.devPanelMode);
  const setMode = useUIStore((s) => s.setDevPanelMode);

  return (
    <div
      role="tablist"
      aria-label="开发模式左侧栏内容"
      className="flex items-stretch"
      style={{ borderTop: "1px solid #e0e0e0" }}
    >
      {OPTIONS.map((opt) => {
        const active = opt.id === mode;
        return (
          <button
            key={opt.id}
            type="button"
            role="tab"
            aria-selected={active}
            title={opt.hint}
            onClick={() => setMode(opt.id)}
            className="flex flex-1 flex-col items-center gap-0.5 px-1 py-1.5 transition-colors hover:bg-[#e8e8e8]"
            style={{
              color: active ? "#0451a5" : "#616161",
              backgroundColor: active ? "#ffffff" : "transparent",
              borderBottom: active
                ? "2px solid #007acc"
                : "2px solid transparent",
              fontWeight: active ? 600 : 400,
            }}
          >
            <span aria-hidden="true" className="text-[13px] leading-none">
              {opt.icon}
            </span>
            <span className="text-[10px] leading-none">{opt.label}</span>
          </button>
        );
      })}
    </div>
  );
}
