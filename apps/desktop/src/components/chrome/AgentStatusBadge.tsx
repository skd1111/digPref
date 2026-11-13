/**
 * AgentStatusBadge —— Agent 运行态徽标（顶栏 TopBar / 状态栏 StatusBar 共用）。
 *
 * 为什么抽出公共组件（2026-11-06）：
 *   此前 TopBar / StatusBar 各自维护一份 AGENT_LABEL 常量表，状态文案与图标
 *   会漂移（TopBar 把 unknown 写成「未连接」、StatusBar 写成「未知」），
 *   且两处都要单独接动画。此处改为单源：状态 → { 文案 / 图标 / 配色 }。
 *
 * 三种可见形态：
 *   运行中（busy）   → 动态 SVG：缺口弧自转 + 核心点呼吸（黑，2026-11-09 用户要求去绿色）
 *   未活动（idle/ready）→ 静态待机圆环（灰）—— Agent 活着但当前没有任务在跑
 *   未就绪（error/unknown）→ 静态叉号圆圈（红 / 未探测时为灰）—— Agent 挂了或探不通
 *
 * 动画类名 .agent-status-arc / .agent-status-core 定义在 styles/globals.css。
 */
import { useUIStore } from '@/store/uiStore';
import {
  AgentIdleIcon,
  AgentRunningIcon,
  ErrorCircleIcon,
  type IconComponent,
} from '@/components/ui/Icons';

/** Agent 运行态元信息：文案 + 图标 + 配色 */
export interface AgentStatusMeta {
  /** 状态文案（不含 "Agent: " 前缀） */
  label: string;
  /** 图标：运行中为动态 SVG，其余为静态图标 */
  Icon: IconComponent;
  /** 图标与文字配色（currentColor 继承） */
  color: string;
  /** 悬浮提示 */
  title: string;
}

/**
 * 状态 → 展示元信息 单源映射。
 *
 * busy   → 运行中：正在处理任务，动态动画图标
 * ready  → 未活动：/health 通但当前无任务（Agent 进程正常）
 * idle   → 未活动：一次任务刚结束、等待下一轮健康轮询
 * error  → 未就绪：/health 探不通，Agent 进程挂了
 * unknown→ 未就绪：尚未完成首次探测（用灰色，避免启动瞬间误报红色）
 */
export const AGENT_STATUS_META: Record<
  'idle' | 'busy' | 'error' | 'ready' | 'unknown',
  AgentStatusMeta
> = {
  busy: {
    label: '运行中',
    Icon: AgentRunningIcon,
    // 2026-11-09（用户要求）：运行态由科技绿改为简洁黑（全局统一，chat 思考动效同步黑化）
    color: '#1f1f1f',
    title: 'Agent 正在处理任务',
  },
  ready: {
    label: '未活动',
    Icon: AgentIdleIcon,
    color: '#616161',
    title: 'Agent 进程正常，当前没有任务在运行',
  },
  idle: {
    label: '未活动',
    Icon: AgentIdleIcon,
    color: '#616161',
    title: 'Agent 进程正常，当前没有任务在运行',
  },
  error: {
    label: '未就绪',
    Icon: ErrorCircleIcon,
    color: '#cd3131',
    title: 'Agent 进程未就绪（健康检查失败，请确认 Agent 已启动）',
  },
  unknown: {
    label: '未就绪',
    Icon: ErrorCircleIcon,
    color: '#8a8a8a',
    title: '尚未完成 Agent 健康探测',
  },
};

/**
 * Agent 状态徽标：动态图标 + 文案（如「运行中」）。
 *
 * 图标随状态切换：运行中为动态 SVG（缺口弧自转 + 核心呼吸），
 * 未活动 / 未就绪为静态图标。配色随状态（黑 / 灰 / 红）。
 */
export function AgentStatusBadge({
  size = 13,
  showLabel = true,
}: {
  /** 图标尺寸（px） */
  size?: number;
  /** 是否显示文案（false 时只留图标） */
  showLabel?: boolean;
}): JSX.Element {
  const agentStatus = useUIStore((s) => s.agentStatus);
  const meta = AGENT_STATUS_META[agentStatus];
  const Icon = meta.Icon;
  return (
    <span
      className="inline-flex items-center gap-1 whitespace-nowrap"
      style={{ color: meta.color }}
      title={meta.title}
      data-testid="agent-status-badge"
    >
      <span className="flex items-center" aria-hidden="true">
        <Icon size={size} />
      </span>
      {showLabel && <span>{`Agent: ${meta.label}`}</span>}
    </span>
  );
}
