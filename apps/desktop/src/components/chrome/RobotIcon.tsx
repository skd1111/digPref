/**
 * RobotIcon —— EAIDE 品牌小机器人（内联 SVG）。
 *
 * 与 [apps/desktop/src-tauri/icons/generate_robot_icon.py](../src-tauri/icons/generate_robot_icon.py)
 * 配色完全一致：深色背景 #1e1e1e / 头部 #2d2d30 / 边框 #007acc / 眼睛 #4ec9b0
 *
 * 用法：
 *   <RobotIcon size={18} />          // MenuBar 品牌标识
 *   <RobotIcon size={32} />          // About 页面
 */
interface RobotIconProps {
  size?: number;
  className?: string;
}

export function RobotIcon({ size = 18, className }: RobotIconProps): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-label="EAIDE"
    >
      {/* 圆角背景 + 蓝色边框 */}
      <rect
        x="1"
        y="1"
        width="30"
        height="30"
        rx="6"
        ry="6"
        fill="#1e1e1e"
        stroke="#007acc"
        strokeWidth="1.5"
      />

      {/* 天线（左右各一） */}
      <line x1="10" y1="3" x2="8" y2="6" stroke="#b4b4b4" strokeWidth="0.8" />
      <circle cx="8" cy="3" r="1" fill="#b4b4b4" />
      <line x1="22" y1="3" x2="24" y2="6" stroke="#b4b4b4" strokeWidth="0.8" />
      <circle cx="24" cy="3" r="1" fill="#b4b4b4" />

      {/* 头部圆角矩形 */}
      <rect
        x="6"
        y="7"
        width="20"
        height="20"
        rx="4"
        ry="4"
        fill="#2d2d30"
        stroke="#5a5a5f"
        strokeWidth="0.5"
      />

      {/* 头部高光（顶部细线） */}
      <line x1="9" y1="10" x2="23" y2="10" stroke="#5a5a5f" strokeWidth="0.5" />

      {/* 眼睛（青色大圆） */}
      <circle cx="11" cy="16" r="3" fill="#4ec9b0" />
      <circle cx="21" cy="16" r="3" fill="#4ec9b0" />
      {/* 眼睛高光（小白点） */}
      <circle cx="10.2" cy="15" r="0.7" fill="#ffffff" />
      <circle cx="20.2" cy="15" r="0.7" fill="#ffffff" />

      {/* 嘴（微笑弧线） */}
      <path
        d="M 11 21 Q 16 25 21 21"
        stroke="#4ec9b0"
        strokeWidth="1.2"
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  );
}
