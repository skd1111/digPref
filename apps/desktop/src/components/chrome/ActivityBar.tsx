/**
 * ActivityBar — VSCode 最左侧的 48px 图标列。
 *
 * 行为：
 *   - 鼠标悬停：高亮 + 提示
 *   - 单击：把对应面板"固定"到主区（Explorer / Search / Source Control / Run / Extensions）
 *   - 活动项：左侧 2px 蓝条 + 前景高亮
 *
 * 实际面板内容由父组件根据 `active` 状态传入。这里只做 chrome。
 */
import { useState } from 'react';
import { useCollabStore, selectMentionCount } from '@/store/collabStore';
import { type ActivityId } from '@/store/uiStore';

// V1 收敛（2026-07-29）：ActivityId 单源定义在 `apps/desktop/src/store/uiStore.ts`，
// 本文件 import 而非重定义。新增 activity 必须改 uiStore.ts + ActivityBar.tsx
// ITEMS 数组 + WorkspaceLayout.tsx TITLES / Outlet 分支 + SideBar early-return。
// ITEMS / TITLES 可用 `satisfies ActivityId[]` / `satisfies Record<ActivityId, string>`
// 在编译期挡住漏改。

interface ActivityItem {
  id: ActivityId;
  label: string;
  /** 简单 unicode 符号作占位图标（生产可换 react-icons / lucide） */
  icon: string;
  /** 是否显示红点 badge（Phase 9 @ 我的未读数） */
  badge?: 'mentionCount';
}

const ITEMS: ActivityItem[] = [
  { id: 'explorer', label: 'Explorer\n系统资产', icon: '⎇' },
  { id: 'search', label: 'Search\n搜索', icon: '⌕' },
  { id: 'source-control', label: 'Source Control\n审计 / 版本', icon: '⎇' },
  { id: 'run-debug', label: 'Run and Debug\n运行 / 调试', icon: '▶' },
  { id: 'extensions', label: 'Extensions\n扩展', icon: '⊞' },
  { id: 'collab', label: 'Collaboration\n任务级协作', icon: '💬', badge: 'mentionCount' },
  // Phase 2F V0 收尾 (2026-07-28)：代码符号搜索顶级入口
  // 与 search activity 顶 tab 的 symbol 模式视觉一致，全屏渲染 320px 双栏（SideBar 折叠）
  { id: 'code-nav', label: 'Code Nav\n代码符号', icon: '⌘' },
  // Phase 6 V1.5 (2026-07-31)：会话管理顶级入口
  // 复用 SideBar 280px 宽度，SessionsPanel 渲染在侧栏
  { id: 'sessions', label: 'Sessions\n会话管理', icon: '🗂️' },
];

interface Props {
  active: ActivityId;
  onChange: (id: ActivityId) => void;
}

export function ActivityBar({ active, onChange }: Props): JSX.Element {
  // Phase 9：协作图标显示 @ 我的未读数
  const mentionCount = useCollabStore((s) => s.contexts.length) > 0 ? selectMentionCount() : 0;
  return (
    <nav
      className="flex w-[48px] flex-col items-stretch select-none"
      style={{ backgroundColor: '#f3f3f3' }}
    >
      {ITEMS.map((item) => {
        const isActive = item.id === active;
        const showBadge = item.badge === 'mentionCount' && mentionCount > 0;
        const badgeLabel = mentionCount > 9 ? '9+' : String(mentionCount);
        return (
          <button
            key={item.id}
            type="button"
            title={item.label}
            onClick={() => onChange(item.id)}
            className="relative flex h-[48px] w-[48px] items-center justify-center text-lg transition-colors hover:text-gray-900"
            style={{
              color: isActive ? '#1f1f1f' : '#616161',
              borderLeft: isActive ? '2px solid #007acc' : '2px solid transparent',
            }}
          >
            {item.icon}
            {showBadge && (
              <span
                className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[9px] font-bold text-white"
                style={{ backgroundColor: '#6366f1', boxShadow: '0 0 0 1px #d0d0d0' }}
              >
                {badgeLabel}
              </span>
            )}
          </button>
        );
      })}

      <div className="flex-1" />

      {/* 底部账号按钮（占位） */}
      <button
        type="button"
        title="Account\n账户"
        className="flex h-[48px] w-[48px] items-center justify-center text-lg transition-colors hover:text-gray-900"
        style={{ color: '#616161' }}
      >
        ◉
      </button>
      <button
        type="button"
        title="Settings\n设置"
        onClick={() => {
          // 跳到 /settings 路由
          window.history.pushState({}, '', '/settings');
          window.dispatchEvent(new PopStateEvent('popstate'));
        }}
        className="flex h-[48px] w-[48px] items-center justify-center text-lg transition-colors hover:text-gray-900"
        style={{ color: '#616161' }}
      >
        ⚙
      </button>
    </nav>
  );
}

/**
 * 状态提升：默认 explorer。在 WorkspaceLayout 持有。
 */
export function useActivityBar(defaultId: ActivityId = 'explorer'): [ActivityId, (id: ActivityId) => void] {
  const [active, setActive] = useState<ActivityId>(defaultId);
  return [active, setActive];
}
