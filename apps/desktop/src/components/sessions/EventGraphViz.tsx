/**
 * EventGraphViz —— Phase 6 V1.5 事件哈希链简易可视化。
 *
 * 用纯 SVG（不依赖 d3）画时序事件图：左侧时间线 + 节点 + 哈希前缀连线。
 * 设计目标：让用户直观看到事件链结构 + 验证 SHA-256 完整性。
 *
 * V1.5 简易实现：节点圆形 + 时间线 + hash 切片。
 * V2 可选升级：d3-force-directed graph 视图（节点 = event，边 = hash chain）。
 */
interface Entry {
  id: number;
  event_type: string;
  hash: string;
  prev_hash: string;
  actor: string;
  created_at: number;
}

interface Props {
  entries: Entry[];
}

export function EventGraphViz({ entries }: Props): JSX.Element {
  if (entries.length === 0) {
    return (
      <div
        className="rounded p-4 text-center text-xs"
        style={{ backgroundColor: '#f3f3f3', color: '#616161' }}
      >
        暂无事件
      </div>
    );
  }

  // 颜色映射事件类型
  const colorByType: Record<string, string> = {
    created: '#6a9955',
    branched: '#0b6bcb',
    shared: '#c586c0',
    exported: '#795e26',
    compressed: '#0451a5',
    checkpoint: '#059669',
    message_appended: '#616161',
    title_changed: '#616161',
    status_changed: '#616161',
  };

  return (
    <div
      className="rounded p-3"
      style={{ backgroundColor: '#f3f3f3', border: '1px solid #c0c0c0' }}
    >
      <svg width="100%" height={Math.max(80, entries.length * 36 + 20)}>
        {/* 时间线主轴 */}
        <line
          x1="40"
          y1={entries.length * 36 + 5}
          x2="40"
          y2="10"
          stroke="#444"
          strokeWidth="2"
        />
        {entries.map((e, idx) => {
          const y = idx * 36 + 20;
          const color = colorByType[e.event_type] ?? '#616161';
          return (
            <g key={e.id}>
              {/* 节点 */}
              <circle cx="40" cy={y} r="8" fill={color} stroke="#fff" strokeWidth="1" />
              <text x="55" y={y + 4} fontSize="11" fill="#333333" fontFamily="monospace">
                {e.event_type}
              </text>
              <text x="160" y={y + 4} fontSize="9" fill="#616161" fontFamily="monospace">
                #{e.id} · {e.hash.slice(0, 12)}…
              </text>
              <text x="320" y={y + 4} fontSize="9" fill="#616161">
                {new Date(e.created_at).toLocaleTimeString()}
              </text>
              {/* 连线（指向下一节点） */}
              {idx < entries.length - 1 && (
                <line
                  x1="40"
                  y1={y + 8}
                  x2="40"
                  y2={(idx + 1) * 36 + 12}
                  stroke="#444"
                  strokeWidth="1"
                  strokeDasharray="3 3"
                />
              )}
            </g>
          );
        })}
      </svg>
      <div className="mt-2 text-xs" style={{ color: '#616161' }}>
        图例：
        <LegendDot color="#6a9955" label="created" />
        <LegendDot color="#9cdcfe" label="branched" />
        <LegendDot color="#c586c0" label="shared" />
        <LegendDot color="#dcdcaa" label="exported" />
        <LegendDot color="#569cd6" label="compressed" />
        <LegendDot color="#4ec9b0" label="checkpoint" />
        <LegendDot color="#616161" label="message_appended" />
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }): JSX.Element {
  return (
    <span className="ml-2 inline-flex items-center gap-1">
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{ backgroundColor: color }}
      />
      {label}
    </span>
  );
}