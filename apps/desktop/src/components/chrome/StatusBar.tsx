/**
 * StatusBar — 底部 22px 状态栏（VSCode 风格）。
 *
 * 数据来源：
 *   - agentStatus: uiStore（由 useAgentStream / 后端轮询设置）
 *   - errorCount / warnCount: uiStore
 *   - cursor: uiStore（用户在 Monaco 里移动光标时更新）
 */
import { useEffect, useState } from 'react';
import { TokenUsageBadge } from '@/components/chrome/TokenUsageBadge';
import { useUIStore } from '@/store/uiStore';

const AGENT_LABEL = {
  idle: 'Agent: 空闲',
  busy: 'Agent: 处理中…',
  error: 'Agent: 未连接',
  ready: 'Agent: 就绪',
  unknown: 'Agent: 未知',
} as const;

export function StatusBar(): JSX.Element {
  const agentStatus = useUIStore((s) => s.agentStatus);
  const errorCount = useUIStore((s) => s.errorCount);
  const warnCount = useUIStore((s) => s.warnCount);
  const cursorLine = useUIStore((s) => s.cursorLine);
  const cursorCol = useUIStore((s) => s.cursorCol);
  const toggleCommandPalette = useUIStore((s) => s.toggleCommandPalette);

  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);

  return (
    <div
      className="flex h-[22px] select-none items-center justify-between px-2 text-2xs"
      style={{ backgroundColor: '#f3f3f3', color: '#1f1f1f' }}
    >
      {/* 左侧 */}
      <div className="flex items-center gap-0">
        <StatusItem icon="⎇" label="main" />
        <StatusItem icon="↻" label="0 ↓ 0 ↑" />
        <StatusItem
          icon="❗"
          label={errorCount > 0 ? String(errorCount) : '0'}
          onClick={toggleCommandPalette}
        />
        <StatusItem icon="⚠" label={String(warnCount)} />
        <StatusItem icon="🔔" label="0" />
        <StatusItem icon="💬" label={AGENT_LABEL[agentStatus]} />
        {/* Token 用量：实时速率（↑上传/↓下载）+ 当日总量，2s 轮询；悬浮向上弹明细卡片 */}
        <span className="flex h-full items-center px-2">
          <TokenUsageBadge placement="top" />
        </span>
      </div>

      {/* 右侧 */}
      <div className="flex items-center gap-0">
        <StatusItem label={`Ln ${cursorLine}, Col ${cursorCol}`} />
        <StatusItem label="Spaces: 4" />
        <StatusItem label="UTF-8" />
        <StatusItem label="LF" />
        <StatusItem label="Markdown" />
        <StatusItem label="🔍 100%" />
        <StatusItem
          label={now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
        />
      </div>
    </div>
  );
}

function StatusItem({
  icon,
  label,
  onClick,
}: {
  icon?: string;
  label: string;
  onClick?: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-full items-center gap-1 px-2 transition-colors hover:bg-gray-200"
    >
      {icon && <span className="text-[10px]">{icon}</span>}
      <span>{label}</span>
    </button>
  );
}
