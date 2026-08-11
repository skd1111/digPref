/**
 * AiStatus —— aicss.dev 风格的 AI 状态组件（2026-08-10，所有模式共享）。
 *
 * 三件套：
 *   - AiThinkingIndicator：思考中（流光文字 + 旋转星芒 orb + 呼吸光晕）
 *   - AiSearchIndicator：  联网搜索/检索（查询词 + 旋转地球 → 完成勾，来源可折叠）
 *   - AiTodoList：         任务列表（To-dos 卡片，n/N 进度 + 勾选样式）
 *
 * 使用方：CenterChatFlow（开发/数据等模式）、ExpertWorkflowPanel（运营模式）、
 * ChatMessage（kind='search' 消息）。
 */
import { useState } from "react";

// ---------------------------------------------------------------------------
// 思考中
// ---------------------------------------------------------------------------

/**
 * 思考中指示器：旋转星芒 + 流光文字 + 三个跳动圆点。
 * label 可定制（默认「思考中」，专家审核场景用「审核中」等）。
 */
export function AiThinkingIndicator({
  label = "思考中",
  compact = false,
}: {
  label?: string;
  /** 紧凑模式：去掉角色前缀行，用于卡片内嵌 */
  compact?: boolean;
}): JSX.Element {
  return (
    <div className={compact ? "" : "mb-3"}>
      {!compact && (
        <div className="mb-0.5 text-2xs" style={{ color: "#9ca3af" }}>
          assistant
        </div>
      )}
      <div
        className="ai-thinking inline-flex items-center gap-2 rounded-full border px-3 py-1.5"
        style={{ borderColor: "#e7e5e4", backgroundColor: "#fafaf9" }}
        aria-label={label}
      >
        <span className="ai-thinking-orb flex-shrink-0" aria-hidden="true">
          <svg
            className="animate-thinking-spark"
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
          >
            <path
              d="M12 2.5l1.9 6.1a1.5 1.5 0 0 0 .95.95l6.1 1.9-6.1 1.9a1.5 1.5 0 0 0-.95.95L12 20.5l-1.9-6.1a1.5 1.5 0 0 0-.95-.95l-6.1-1.9 6.1-1.9a1.5 1.5 0 0 0 .95-.95L12 2.5z"
              fill="#10a37f"
              opacity="0.9"
            />
          </svg>
        </span>
        <span className="animate-thinking-shimmer text-ui font-medium">
          {label}
        </span>
        <span className="inline-flex items-end gap-1" aria-hidden="true">
          {[0, 150, 300].map((delay) => (
            <span
              key={delay}
              className="inline-block h-1.5 w-1.5 animate-bounce rounded-full"
              style={{
                backgroundColor: "#10a37f",
                opacity: 0.65,
                animationDelay: `${delay}ms`,
              }}
            />
          ))}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 联网搜索 / 检索
// ---------------------------------------------------------------------------

export interface AiSearchSource {
  title: string;
  /** 来源域名或出处说明（如 auth0.com、知识库） */
  origin?: string;
}

/**
 * 线框地球动效（aicss.dev WebSearch 同款，2026-08-10）：
 * 六条经线相位各差 1/6 周期，视觉上读作一个旋转的球体。
 */
const GLOBE_MERIDIAN =
  'M6.057 11.565 C2.081 11.565 0.371 8.159 0.371 5.964 C0.371 3.642 2.152 0.329 6.05 0.329';
const GLOBE_SHAPES = [
  GLOBE_MERIDIAN,
  'M6.012 11.55 C4.575 10.496 3.333 8.116 3.321 5.964 C3.307 3.399 4.974 0.977 6.012 0.329',
  'M6.012 11.55 C7.211 10.781 8.715 8.287 8.715 5.964 C8.715 3.399 7.24 1.233 6.012 0.329',
  'M6.012 11.55 C9.677 11.55 11.65 8.487 11.65 5.964 C11.65 3.499 9.748 0.329 6.012 0.329',
];

function SearchGlobe(): JSX.Element {
  const values = [...GLOBE_SHAPES, GLOBE_MERIDIAN].join(';');
  return (
    <svg
      viewBox="0 0 12 12"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="0.85"
      strokeLinecap="round"
      style={{ overflow: 'visible' }}
      aria-hidden="true"
    >
      <circle cx="6" cy="6" r="5.7" opacity="0.9" />
      <line x1="0.3" y1="6" x2="11.7" y2="6" opacity="0.9" />
      {['0s', '-1.2s', '-2.4s', '-3.6s', '-4.8s', '-6s'].map((begin) => (
        <path key={begin} d={GLOBE_MERIDIAN} opacity="0">
          <animate
            attributeName="d"
            dur="7.2s"
            begin={begin}
            repeatCount="indefinite"
            calcMode="spline"
            keyTimes="0;0.25;0.5;0.75;1"
            keySplines="0.42 0 0.58 1;0.42 0 0.58 1;0.42 0 0.58 1;0.42 0 0.58 1"
            values={values}
          />
          <animate
            attributeName="opacity"
            dur="7.2s"
            begin={begin}
            repeatCount="indefinite"
            calcMode="linear"
            keyTimes="0;0.05;0.7;0.75;1"
            values="0;0.9;0.9;0;0"
          />
        </path>
      ))}
    </svg>
  );
}

/**
 * 搜索状态卡片（aicss.dev WebSearch 风格，2026-08-10 升级）：
 *   - running：旋转线框地球 + 「正在搜索」流光文字 + 查询词
 *   - done：绿勾 + 「搜索完成」+ 可折叠来源列表（左侧竖轨 + 逐项状态圆点）
 */
export function AiSearchIndicator({
  query,
  sources = [],
  done = false,
}: {
  query: string;
  sources?: AiSearchSource[];
  done?: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(true);
  return (
    <div className="mb-3">
      <div
        className="max-w-[480px] rounded-lg border"
        style={{ borderColor: '#e7e5e4', backgroundColor: '#fafaf9' }}
      >
        <div className="flex items-center gap-2 px-3 py-2">
          <span
            className="flex-shrink-0"
            style={{ color: done ? '#10a37f' : '#0451a5' }}
            aria-hidden="true"
          >
            {done ? (
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
              </svg>
            ) : (
              <SearchGlobe />
            )}
          </span>
          <span className="flex min-w-0 flex-1 items-baseline gap-1">
            <span
              className={done ? 'flex-shrink-0 text-2xs' : 'animate-thinking-shimmer flex-shrink-0 text-2xs font-medium'}
              style={{ color: done ? '#6b7280' : undefined }}
            >
              {done ? '搜索完成' : '正在搜索'}
            </span>
            <span className="truncate text-ui" style={{ color: '#202124' }} title={query}>
              “{query}”
            </span>
          </span>
          {sources.length > 0 && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="flex flex-shrink-0 items-center gap-1 text-2xs"
              style={{ color: '#9ca3af' }}
              aria-expanded={open}
            >
              {sources.length} 个来源
              <span
                style={{
                  display: 'inline-block',
                  transform: open ? 'none' : 'rotate(-90deg)',
                  transition: 'transform 0.15s',
                }}
              >
                ▾
              </span>
            </button>
          )}
        </div>
        {open && sources.length > 0 && (
          <div
            className="border-t px-3 pb-2 pt-1.5"
            style={{ borderColor: '#f0efed' }}
          >
            {/* 左侧竖轨 + 逐项状态圆点（官网同款布局） */}
            <div className="flex gap-2">
              <span
                className="my-1 w-px flex-shrink-0"
                style={{ backgroundColor: '#e7e5e4' }}
                aria-hidden="true"
              />
              <ul className="min-w-0 flex-1 space-y-1">
                {sources.map((s, i) => (
                  <li key={i} className="flex min-w-0 items-center gap-1.5 text-2xs">
                    <span
                      className="flex-shrink-0"
                      style={{ color: done ? '#10a37f' : '#0451a5' }}
                      aria-hidden="true"
                    >
                      {done ? (
                        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                          <path d="m4.5 12.75 6 6 9-13.5" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor">
                          <circle cx="12" cy="12" r="9" strokeWidth="1.8" strokeDasharray="1.8 3.6" strokeLinecap="round" />
                        </svg>
                      )}
                    </span>
                    <span className="truncate" style={{ color: '#202124' }}>
                      {s.title}
                    </span>
                    {s.origin && (
                      <span className="flex-shrink-0" style={{ color: '#9ca3af' }}>
                        · {s.origin}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 任务列表（To-dos）
// ---------------------------------------------------------------------------

export interface AiTodoItem {
  text: string;
  done: boolean;
}

/**
 * 任务列表卡片（aicss To-do List 风格）：标题 + n/N 进度 + 勾选项。
 * Markdown 任务列表（- [ ] / - [x]）在 Markdown.tsx 中解析后渲染为本组件。
 */
export function AiTodoList({
  items,
  title = "任务清单",
}: {
  items: AiTodoItem[];
  title?: string;
}): JSX.Element {
  const doneCount = items.filter((x) => x.done).length;
  const allDone = items.length > 0 && doneCount === items.length;
  return (
    <div
      className="my-2 max-w-[480px] rounded-lg border"
      style={{
        borderColor: allDone ? "#10a37f55" : "#e7e5e4",
        backgroundColor: "#fafaf9",
      }}
    >
      <div
        className="flex items-center justify-between border-b px-3 py-1.5"
        style={{ borderColor: "#f0efed" }}
      >
        <span
          className="text-2xs font-semibold uppercase tracking-wider"
          style={{ color: "#6b7280" }}
        >
          ☑ {title}
        </span>
        <span
          className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
          style={{
            backgroundColor: allDone ? "#10a37f22" : "#f3f4f6",
            color: allDone ? "#10a37f" : "#6b7280",
          }}
        >
          {doneCount}/{items.length}
        </span>
      </div>
      <ul className="px-3 py-2">
        {items.map((item, i) => (
          <li key={i} className="flex items-start gap-2 py-0.5 text-ui">
            <span
              className="mt-0.5 flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center rounded border text-[9px] font-bold"
              style={{
                borderColor: item.done ? "#10a37f" : "#d1d5db",
                backgroundColor: item.done ? "#10a37f" : "#ffffff",
                color: "#ffffff",
              }}
              aria-hidden="true"
            >
              {item.done ? "✓" : ""}
            </span>
            <span
              style={{
                color: item.done ? "#9ca3af" : "#202124",
                textDecoration: item.done ? "line-through" : "none",
              }}
            >
              {item.text}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
