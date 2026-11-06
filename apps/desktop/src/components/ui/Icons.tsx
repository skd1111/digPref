/**
 * Icons —— 单色线性 SVG 图标库（2026-11-04）。
 *
 * 为什么自绘而不是引 react-icons / lucide：
 *   EAIDE 是企业内网本地化部署，第三方 UI 依赖要走内网 npm 镜像，
 *   新增依赖的落地风险高于收益。这里手写一套零依赖图标。
 *
 * 统一规范（对齐 VSCode codicon 的观感）：
 *   - 24×24 viewBox，stroke 描边（fill: none）
 *   - stroke="currentColor" —— 颜色由外层容器的 color 继承，
 *     因此按钮的 active / hover 配色自动生效，无需给图标单独配色
 *   - stroke-width 1.75、round linecap / linejoin，小尺寸下不糊
 *   - aria-hidden：图标纯装饰，语义由外层按钮的 title / aria-label 承担
 *
 * 用法：<FolderIcon size={24} />（size 同时决定 width/height）
 */

export interface IconProps {
  /** 渲染尺寸（px），默认 24 */
  size?: number;
  /** 附加 className（如调整对齐 / 透明度） */
  className?: string;
}

/** 所有图标共用的 svg 属性 */
const BASE = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: false,
} as const;

/** 文件夹 —— Explorer / 文件列表 */
export function FolderIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3 7.5A2 2 0 0 1 5 5.5h3.4a2 2 0 0 1 1.5.7l1 1.3H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-8.5Z" />
    </svg>
  );
}

/** 放大镜 —— Search */
export function SearchIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.6-3.6" />
    </svg>
  );
}

/** Git 分支 —— Source Control / 审计与版本 */
export function GitBranchIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="8" r="2.5" />
      <path d="M6 8.5v7" />
      <path d="M18 10.5c0 3.2-3.2 3.8-6 4.2" />
    </svg>
  );
}

/** 播放 —— Run and Debug */
export function PlayIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M7.5 5.2v13.6L19 12 7.5 5.2Z" />
    </svg>
  );
}

/** 四宫格 —— Extensions / 扩展 */
export function BlocksIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" />
    </svg>
  );
}

/** 对话气泡 —— Collaboration / 任务级协作 */
export function MessageIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M20.5 11.6a7.5 7.5 0 0 1-7.9 7.5c-.7 0-1.4-.1-2-.3L5 21l1.2-4.6a7.5 7.5 0 0 1-.7-3.3 7.5 7.5 0 0 1 7.5-7.5h.6a7.5 7.5 0 0 1 6.9 6Z" />
    </svg>
  );
}

/** 花括号 —— Code Nav / 代码符号 */
export function BracesIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M9 4H8a2 2 0 0 0-2 2v3a2 2 0 0 1-2 2 2 2 0 0 1 2 2v3a2 2 0 0 0 2 2h1" />
      <path d="M15 4h1a2 2 0 0 1 2 2v3a2 2 0 0 0 2 2 2 2 0 0 0-2 2v3a2 2 0 0 1-2 2h-1" />
    </svg>
  );
}

/** 时钟回转 —— Sessions / 会话管理 */
export function HistoryIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1" />
      <path d="M3.5 4.5V9H8" />
      <path d="M12 7.8V12l3 1.8" />
    </svg>
  );
}

/** 剪贴板清单 —— Requirements / 需求工作台 */
export function ClipboardIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M9 4.5H7a2 2 0 0 0-2 2V19a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6.5a2 2 0 0 0-2-2h-2" />
      <rect x="9" y="2.5" width="6" height="4" rx="1" />
      <path d="M8.5 12.5h7M8.5 16.5h4.5" />
    </svg>
  );
}

/** 翻开的书 —— Data Dictionary / 数据字典 */
export function BookIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 6.8C10.4 5.4 7.8 4.8 4 5.2v12.6c3.8-.4 6.4.2 8 1.6 1.6-1.4 4.2-2 8-1.6V5.2c-3.8-.4-6.4.2-8 1.6Z" />
      <path d="M12 6.8v12.6" />
    </svg>
  );
}

/** 数据库 —— 系统资产（DB / API / SSH / RPA） */
export function DatabaseIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <ellipse cx="12" cy="6" rx="7.5" ry="3" />
      <path d="M4.5 6v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6" />
      <path d="M4.5 12v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" />
    </svg>
  );
}

/** 组件（方块 + 连线）—— 系统功能点 */
export function ComponentIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" />
      <path d="M10.5 7h2.5a2 2 0 0 1 2 2v4.5" />
    </svg>
  );
}

/** 用户 —— 账户 */
export function UserIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="10" r="3" />
      <path d="M6.3 18.6a6.5 6.5 0 0 1 11.4 0" />
    </svg>
  );
}

/** 齿轮 —— 设置 */
export function SettingsIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.1 14.6a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5v.1a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3h.1a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8v.1a1.6 1.6 0 0 0 1.5 1h.1a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z" />
    </svg>
  );
}

// —— 展开折叠 ————————————————————————————————————————————————

/** 向下箭头（展开） */
export function ChevronDownIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

/** 向上箭头（收起） */
export function ChevronUpIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="m6 15 6-6 6 6" />
    </svg>
  );
}

/** 向右箭头（折叠 / 下一级） */
export function ChevronRightIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

// —— 基础动作 ————————————————————————————————————————————————

/** X —— 关闭 / 移除 */
export function CloseIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

/** 加号 —— 新建 / 添加 */
export function PlusIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

/** 环形箭头 —— 刷新 / 重试 / 重启 */
export function RefreshIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M20 11.5a8 8 0 1 0-1.6 4.9" />
      <path d="M20 4v7.5h-7.5" />
    </svg>
  );
}

/** 勾 —— 完成 / 成功 */
export function CheckIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="m4.5 12.5 5 5 10-11" />
    </svg>
  );
}

/** 勾选圆圈 —— 选中态 */
export function CheckCircleIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m8.3 12.4 2.6 2.6 4.8-5.4" />
    </svg>
  );
}

/** 空心圆 —— 未选中 */
export function CircleIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="8.5" />
    </svg>
  );
}

/** 实心圆点 —— 状态指示 */
export function DotIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className} fill="currentColor" stroke="none">
      <circle cx="12" cy="12" r="4.5" />
    </svg>
  );
}

/** 四角箭头 —— 放大 / 全屏 */
export function MaximizeIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M4 9V4h5M20 15v5h-5M20 9V4h-5M4 15v5h5" />
    </svg>
  );
}

/** 垃圾桶 —— 删除 */
export function TrashIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M4 7h16M9.5 7V5.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V7M6.5 7l.9 12.2a1.8 1.8 0 0 0 1.8 1.8h5.6a1.8 1.8 0 0 0 1.8-1.8L17.5 7M10 11v6M14 11v6" />
    </svg>
  );
}

/** 铅笔 —— 编辑 */
export function PencilIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M4 20.5 4.8 17l10.4-10.4a2.1 2.1 0 0 1 3 3L7.8 20 4 20.5Z" />
      <path d="m14 6 3 3" />
    </svg>
  );
}

/** 眼睛 —— 预览 / 查看 */
export function EyeIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

/** 软盘 —— 保存 */
export function SaveIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M5 3.5h11L20 7.5v13H5V3.5Z" />
      <path d="M8.5 3.5v5h7v-5M8.5 20.5v-6h7v6" />
    </svg>
  );
}

/** 向下箭头入托盘 —— 导入 / 下载 */
export function DownloadIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 3.5v11M7.5 10.5 12 15l4.5-4.5M4 20.5h16" />
    </svg>
  );
}

/** 向上箭头出托盘 —— 上传 / 导出 */
export function UploadIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 15.5v-12M7.5 8 12 3.5 16.5 8M4 20.5h16" />
    </svg>
  );
}

/** 向上箭头 —— 上移 / 发送 */
export function ArrowUpIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 20V4.5M6 10.5 12 4.5l6 6" />
    </svg>
  );
}

/** 向下箭头 —— 下移 */
export function ArrowDownIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 4v15.5M6 13.5 12 19.5l6-6" />
    </svg>
  );
}

// —— 业务语义 ————————————————————————————————————————————————

/** 灯泡 —— 建议 / 提示 */
export function BulbIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M9.5 18h5M10.5 21h3M12 3a6 6 0 0 0-3.5 10.9V16h7v-2.1A6 6 0 0 0 12 3Z" />
    </svg>
  );
}

/** 定位针 —— 定位 / 跳转 */
export function MapPinIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 21.5s7-6.4 7-11.2a7 7 0 1 0-14 0c0 4.8 7 11.2 7 11.2Z" />
      <circle cx="12" cy="10" r="2.5" />
    </svg>
  );
}

/** 回形针 —— 附件 */
export function PaperclipIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M20 11.5 12.5 19a4.5 4.5 0 0 1-6.4-6.4l7.5-7.5a3 3 0 0 1 4.3 4.3l-7.5 7.5a1.5 1.5 0 0 1-2.1-2.1L15 9" />
    </svg>
  );
}

/** 锁 —— 只读 / 安全 */
export function LockIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="5" y="10.5" width="14" height="10" rx="2" />
      <path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3" />
    </svg>
  );
}

/** 钥匙 —— 凭证 */
export function KeyIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="7.5" cy="12" r="3.5" />
      <path d="M11 12h9M17.5 12v3M14.5 12v2.5" />
    </svg>
  );
}

/** 地球 —— REST API */
export function GlobeIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.5 2.6 3.8 5.7 3.8 9S14.5 18.4 12 21c-2.5-2.6-3.8-5.7-3.8-9S9.5 5.6 12 3Z" />
    </svg>
  );
}

/** 机器人 —— AI / Agent */
export function BotIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="4" y="8" width="16" height="12" rx="3" />
      <path d="M12 8V4.5M9 14h.01M15 14h.01M2.5 12.5v3M21.5 12.5v3" />
      <circle cx="12" cy="3.5" r="1.3" />
    </svg>
  );
}

/** 双人 —— 专家团 / 协作 */
export function UsersIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
      <path d="M16 5a3.5 3.5 0 0 1 0 6.6M17.5 20a6.4 6.4 0 0 0-2.1-4.7" />
    </svg>
  );
}

/** 波形 —— 上下文 / 推理强度 */
export function ActivityIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3 12h3l2.5-6.5 4 13L15.5 9l2 3h3.5" />
    </svg>
  );
}

/** 抹布 —— 清理 */
export function EraserIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="m14.8 4.2 5 5L9.5 19.5H4.5l-2-2L14.8 4.2Z" />
      <path d="M9 10 15 16M8 21.5h12" />
    </svg>
  );
}

/** 包裹 —— 交付物 / 资产包 */
export function PackageIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3 8.5 12 4l9 4.5v7L12 20l-9-4.5v-7Z" />
      <path d="M3 8.5 12 13m0 0 9-4.5M12 13v7" />
    </svg>
  );
}

/** 礼物 —— 产物 */
export function GiftIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="3.5" y="9" width="17" height="11.5" rx="1.5" />
      <path d="M3.5 13h17M12 9v11.5" />
      <path d="M12 9S10.6 4.5 8.2 4.5a2.3 2.3 0 0 0 0 4.5H12Zm0 0s1.4-4.5 3.8-4.5a2.3 2.3 0 0 1 0 4.5H12Z" />
    </svg>
  );
}

/** 月亮 —— 纯净对话 */
export function MoonIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z" />
    </svg>
  );
}

/** 闪电 —— 加速 / 性能 */
export function ZapIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M13 2.5 4.5 14H11l-1 7.5L19.5 10H13l0-7.5Z" />
    </svg>
  );
}

/** 表格 —— 数据网格 */
export function TableIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 10h18M3 15h18M10 4v16" />
    </svg>
  );
}

/** 柱状图 —— 评分 / 统计 */
export function BarChartIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3 20.5h18M6.5 20V11M12 20V4.5M17.5 20v-6" />
    </svg>
  );
}

/** 折线图 —— 可视化 */
export function LineChartIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3 20.5h18M6 15l4.5-5.5 3 3L19 6" />
    </svg>
  );
}

/** 调色盘 —— 主题 / 样式 */
export function PaletteIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 3a9 9 0 0 0 0 18c1 0 1.7-.8 1.7-1.7 0-.5-.2-.9-.5-1.2a1.7 1.7 0 0 1 1.3-2.9H16a5 5 0 0 0 5-5c0-4.1-4-7.2-9-7.2Z" />
      <circle cx="8" cy="10" r="1.2" />
      <circle cx="12" cy="7.5" r="1.2" />
      <circle cx="16" cy="9" r="1.2" />
    </svg>
  );
}

/** 插头 —— MCP / 连接 */
export function PlugIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M9 3v6M15 3v6M6 9h12v2.5a6 6 0 0 1-12 0V9ZM12 17.5V21" />
    </svg>
  );
}

/** 盾锁 —— 高风险 / 二次验证 */
export function ShieldIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 3 5 6v6c0 4.6 3 7.9 7 9.2 4-1.3 7-4.6 7-9.2V6l-7-3Z" />
      <path d="M12 10.5v3M12 16.5h.01" />
    </svg>
  );
}

/** 分叉 —— 会话分支 */
export function GitForkIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="18" r="2.5" />
      <path d="M6 8.5v7M18 15.5V12a6 6 0 0 0-6-6H8.5" />
    </svg>
  );
}

/** 扳手 —— 工具调用 */
export function WrenchIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M15.5 3.5a5 5 0 0 0-6.6 6.6L3 16l5 5 5.9-5.9a5 5 0 0 0 6.6-6.6L17 12l-3-3 1.5-5.5Z" />
    </svg>
  );
}

/** 铃铛 —— 提醒 */
export function BellIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M18 16.5V11a6 6 0 1 0-12 0v5.5l-2 2.5h16l-2-2.5ZM10 21.5h4" />
    </svg>
  );
}

/** 外链箭头 —— 在系统 / 外部打开 */
export function ExternalLinkIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M14 4h6v6M20 4l-9.5 9.5M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </svg>
  );
}

/** 沙漏 —— 进行中 / 等待 */
export function HourglassIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M7 3.5h10M7 20.5h10M8 3.5V7c0 2 4 3.5 4 5s-4 3.5-4 5v3.5M16 3.5V7c0 2-4 3.5-4 5" />
    </svg>
  );
}

/** 暂停 —— 手动模式 */
export function PauseIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="7" y="5" width="3.5" height="14" rx="1" />
      <rect x="13.5" y="5" width="3.5" height="14" rx="1" />
    </svg>
  );
}

/** 警告三角 */
export function WarningIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 4 2.8 20h18.4L12 4ZM12 10v4.5M12 17.6h.01" />
    </svg>
  );
}

/** 链接 —— 签名链 / 引用 */
export function LinkIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M10 13.5a4.5 4.5 0 0 0 6.8.5l2-2a4.5 4.5 0 0 0-6.4-6.4l-1.1 1.1M14 10.5a4.5 4.5 0 0 0-6.8-.5l-2 2a4.5 4.5 0 0 0 6.4 6.4l1.1-1.1" />
    </svg>
  );
}

/** 靶心 —— 选区执行 */
export function TargetIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** 文件 */
export function FileIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5L13.5 3Z" />
      <path d="M13.5 3v5.5H19" />
    </svg>
  );
}

/** 打开的文件夹 —— 空态 */
export function FolderOpenIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3 8V6.5a2 2 0 0 1 2-2h3.4a2 2 0 0 1 1.5.7l1 1.3H18a2 2 0 0 1 2 2v1.5" />
      <path d="M3 8h18l-2.1 10.4a2 2 0 0 1-2 1.6H5.1a2 2 0 0 1-2-1.6L3 8Z" />
    </svg>
  );
}

/** 复制 */
export function CopyIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2" />
      <path d="M15.5 8.5v-2a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2" />
    </svg>
  );
}

/** 地球仪 / 指南针 —— 路由 */
export function CompassIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="m15.5 8.5-2 5.5-5.5 2 2-5.5 5.5-2Z" />
    </svg>
  );
}

/** 钱袋 —— 预算 */
export function WalletIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M4 8.5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v.5" />
      <rect x="3" y="9" width="18" height="11.5" rx="2" />
      <circle cx="16.5" cy="14.5" r="1.3" />
    </svg>
  );
}

/** 导航箭头 —— 指南 / 跳转 */
export function NavigationIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3.5 11 20.5 4l-7 16-2.5-6.5L3.5 11Z" />
    </svg>
  );
}

/** 空心方框 —— 多选未选中 */
export function SquareIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="4" y="4" width="16" height="16" rx="3" />
    </svg>
  );
}

/** 方框 + 勾 —— 多选已选中 */
export function CheckSquareIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="m8.5 12.2 2.6 2.6 4.6-5.4" />
    </svg>
  );
}

/** 点赞 —— 回答有帮助 */
export function ThumbsUpIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M7 21V10l4.5-7a2 2 0 0 1 2.7 2.4L13 10h5.5a2 2 0 0 1 2 2.4l-1.4 6.6a2 2 0 0 1-2 1.6H7Z" />
      <path d="M7 10H4.5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1H7" />
    </svg>
  );
}

/** 踩 —— 回答有问题 */
export function ThumbsDownIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M7 3v11l4.5 7a2 2 0 0 0 2.7-2.4L13 14h5.5a2 2 0 0 0 2-2.4l-1.4-6.6a2 2 0 0 0-2-1.6H7Z" />
      <path d="M7 14H4.5a1 1 0 0 0-1-1V4a1 1 0 0 1 1-1H7" />
    </svg>
  );
}

/** 终端窗口 —— Terminal */
export function TerminalIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="2.5" y="4" width="19" height="16" rx="2" />
      <path d="m6.5 9.5 3 2.5-3 2.5M12.5 15h5" />
    </svg>
  );
}

/** 信息圆圈 —— About */
export function InfoCircleIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11.2v5.3M12 7.8h.01" />
    </svg>
  );
}

/** 幼苗 —— 经验库 / 自进化 */
export function SproutIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M12 21v-8.5" />
      <path d="M12 12.5c0-3.3 2.7-6 6-6 0 3.3-2.7 6-6 6Z" />
      <path d="M12 12.5c0-2.2-1.8-4-4-4 0 2.2 1.8 4 4 4Z" />
      <path d="M8 21h8" />
    </svg>
  );
}

/** 叉号圆圈 —— 错误 */
export function ErrorCircleIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m9.2 9.2 5.6 5.6M14.8 9.2l-5.6 5.6" />
    </svg>
  );
}

/** 标旗 —— 需求 / 标记 */
export function FlagIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M5 21V4M5 4.5h11l-2 3.5 2 3.5H5" />
    </svg>
  );
}

/** 房屋 —— 工作台 / 首页 */
export function HomeIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3.5 10.5 12 3.5l8.5 7" />
      <path d="M5.5 9.8V20h13V9.8" />
      <path d="M10 20v-5h4v5" />
    </svg>
  );
}

/** 楼宇 —— 银行业务 / 对公账户 */
export function BuildingIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M4 21V5.5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1V21" />
      <path d="M14 10h5a1 1 0 0 1 1 1v10" />
      <path d="M7 8h4M7 11.5h4M7 15h4" />
      <path d="M2.5 21h19" />
    </svg>
  );
}

/** 带横线的文档 —— 资料预审 / 表单 */
export function FileTextIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M13.5 3.5H6.5a1 1 0 0 0-1 1v15a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V8.5Z" />
      <path d="M13.5 3.5V8.5H18.5" />
      <path d="M8.5 12.5h7M8.5 16h5" />
    </svg>
  );
}

/** 票据 —— 反洗钱 / 凭证 */
export function ReceiptIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M5.5 3.5v17l2.5-1.6 2.5 1.6 2.5-1.6 2.5 1.6 2.5-1.6v-17l-2.5 1.6L13 3.5l-2.5 1.6L8 3.5Z" />
      <path d="M9 9h6M9 12.5h6" />
    </svg>
  );
}

/** 喇叭 —— 营销 / 通知 */
export function MegaphoneIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M3.5 10.5v3a1 1 0 0 0 1 1h2l7 4.5v-15l-7 4.5h-2a1 1 0 0 0-1 1Z" />
      <path d="M17 9.5a4 4 0 0 1 0 5" />
      <path d="M6.5 14.5V19a1 1 0 0 0 1 1h1.5a1 1 0 0 0 1-1v-3" />
    </svg>
  );
}

/** 电话听筒 —— 客服 / 呼叫 */
export function PhoneIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M6 3.5h3l1.5 4-2 1.5a10.5 10.5 0 0 0 5.5 5.5l1.5-2 4 1.5v3a1.5 1.5 0 0 1-1.7 1.5C10.4 17.7 6.3 13.6 4.5 6.2A1.5 1.5 0 0 1 6 4.5Z" />
    </svg>
  );
}

/** 逆时针箭头 —— 重新开始 / 重置 */
export function RotateCcwIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M4 5.5v5h5" />
      <path d="M4.6 10.5a7.8 7.8 0 1 1 .6 6.6" />
    </svg>
  );
}

/** 分享（节点向外箭头）—— 分享 / 导出 */
export function ShareIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="17.5" cy="6" r="2.5" />
      <circle cx="6.5" cy="12" r="2.5" />
      <circle cx="17.5" cy="18" r="2.5" />
      <path d="m8.8 10.8 6.4-3.5M8.8 13.2l6.4 3.5" />
    </svg>
  );
}

/** @ 符号 —— 提及 / @我 */
export function AtSignIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <circle cx="12" cy="12" r="4" />
      <path d="M16 12v1.5a2.5 2.5 0 0 0 5 0V12a9 9 0 1 0-3.5 7.1" />
    </svg>
  );
}

/** 左右分栏 —— 垂直拆分 */
export function ColumnsIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.5" />
      <path d="M12 4.5v15" />
    </svg>
  );
}

/** 上下分栏 —— 水平拆分 */
export function RowsIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.5" />
      <path d="M3.5 12h17" />
    </svg>
  );
}

/** 面板布局 —— 预览 / 分栏切换 */
export function LayoutIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.5" />
      <path d="M13.5 4.5v15" />
      <path d="M13.5 12h7" />
    </svg>
  );
}

/** 大脑 —— 技能 / 智能体 */
export function BrainIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M9.5 4.5A2.5 2.5 0 0 0 7 7a2.5 2.5 0 0 0-1.5 4.5A2.5 2.5 0 0 0 7 16a2.5 2.5 0 0 0 2.5 2.5A2.5 2.5 0 0 0 12 16V7a2.5 2.5 0 0 0-2.5-2.5Z" />
      <path d="M14.5 4.5A2.5 2.5 0 0 1 17 7a2.5 2.5 0 0 1 1.5 4.5A2.5 2.5 0 0 1 17 16a2.5 2.5 0 0 1-2.5 2.5A2.5 2.5 0 0 1 12 16" />
      <path d="M12 7v9" />
    </svg>
  );
}

/** 向左箭头（返回 / 上一级） */
export function ChevronLeftIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="m15 6-6 6 6 6" />
    </svg>
  );
}

/** 归档盒 —— 归档 / 收纳 */
export function ArchiveIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <rect x="3.5" y="4.5" width="17" height="4" rx="1" />
      <path d="M5 8.5v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-10" />
      <path d="M10 12.5h4" />
    </svg>
  );
}

/** 回形箭头 —— 回复 / 撤回 */
export function ReplyIcon({ size = 24, className }: IconProps): JSX.Element {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M9 7.5 4.5 12 9 16.5" />
      <path d="M4.5 12h9a6 6 0 0 1 6 6v1.5" />
    </svg>
  );
}

/** 图标组件类型（供 ITEMS 之类的映射表使用） */
export type IconComponent = (props: IconProps) => JSX.Element;
