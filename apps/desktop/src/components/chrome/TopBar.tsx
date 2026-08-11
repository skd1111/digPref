/**
 * TopBar — 醒目顶部状态栏（位于 MenuBar 下方）。
 *
 * 三个区域：
 *   左：活跃环境指示器（带切换下拉）—— 醒目大徽章
 *   中：工作模式切换（完整 IDE / 运营专家）—— 圆角页签，选中高亮
 *   右：占位（后续可放 Agent 状态 / 知识库 / 快捷操作）
 *
 * 设计目标：用户**一眼能看到当前环境 + 当前模式**，不用去角落找。
 */
import { EnvironmentIndicator } from './EnvironmentIndicator';
import { ModeSwitcher } from './ModeSwitcher';
import { TokenUsageBadge } from './TokenUsageBadge';
import { useUIStore } from '@/store/uiStore';

/** Agent 状态文案 —— 与底部 StatusBar 保持一致，读真实 agentStatus。 */
const AGENT_LABEL = {
  idle: 'Agent: 空闲',
  busy: 'Agent: 处理中…',
  error: 'Agent: 未连接',
  ready: 'Agent: 就绪',
  unknown: 'Agent: 未连接',
} as const;

export function TopBar(): JSX.Element {
  const pendingCollabMentionCount = useUIStore((s) => s.pendingCollabMentionCount);
  const agentStatus = useUIStore((s) => s.agentStatus);
  return (
    <div
      className="flex h-[44px] select-none items-center justify-between border-b px-4"
      style={{
        backgroundColor: '#ececec',
        borderColor: '#e0e0e0',
        boxShadow: '0 1px 0 0 #e0e0e0',
      }}
    >
      {/* 左：环境徽章（稍微放大） */}
      <div className="flex items-center gap-3">
        <span
          className="text-2xs font-semibold uppercase tracking-wider"
          style={{ color: '#616161' }}
        >
          ENV
        </span>
        <EnvironmentIndicator large />
      </div>

      {/* 中：模式切换器（更醒目） */}
      <ModeSwitcher large />

      {/* 右：Token 用量 + Agent 状态 + Phase 9 @ 我的未读数 */}
      <div className="flex items-center gap-2 text-2xs" style={{ color: '#616161' }}>
        {pendingCollabMentionCount > 0 && (
          <span
            className="flex items-center gap-1 rounded px-2 py-0.5 font-semibold"
            style={{ backgroundColor: 'rgba(99, 102, 241, 0.18)', color: '#4f46e5' }}
            title="协作中心 @ 我的未读数（点击 ActivityBar 💬 图标查看）"
          >
            💬 @{pendingCollabMentionCount > 9 ? '9+' : pendingCollabMentionCount}
          </span>
        )}
        {/* Token 用量：实时速率（↑上传/↓下载）+ 当日总量，2s 轮询；悬浮向下弹明细卡片 */}
        <span className="rounded px-2 py-0.5" style={{ backgroundColor: '#e4e4e4' }}>
          <TokenUsageBadge placement="bottom" />
        </span>
        <span className="rounded px-2 py-0.5" style={{ backgroundColor: '#ececec' }}>
          ⚡ {AGENT_LABEL[agentStatus]}
        </span>
      </div>
    </div>
  );
}
