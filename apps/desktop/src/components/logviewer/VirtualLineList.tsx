/**
 * VirtualLineList —— Phase 2F+ V1.5
 *
 * 轻量级虚拟滚动行列表（DIY 实现，零额外依赖）。
 * 支持 100GB 级日志文件的逐行渲染，只 mount 可视区域内的 DOM 节点。
 *
 * Props:
 *   - lines: 当前需要渲染的行
 *   - lineHeight: 每行高度（默认 22px）
 *   - overscan: 预渲染行数（默认 10）
 *   - highlightPattern: 高亮正则（搜索关键词）
 *   - tailMode: 是否自动跟底
 */

import { useRef, useEffect, useState, useCallback } from 'react';

export interface VirtualLineListProps {
  lines: string[];
  lineHeight?: number;
  overscan?: number;
  highlightPattern?: RegExp | null;
  highlightLine?: number | null;
  tailMode?: boolean;
  className?: string;
}

/** 5 级日志级别着色 */
const LEVEL_COLORS: Record<string, string> = {
  FATAL: 'text-red-700 dark:text-red-300 font-bold',
  ERROR: 'text-red-600 dark:text-red-400 font-semibold',
  WARN: 'text-yellow-600 dark:text-yellow-400',
  INFO: 'text-blue-600 dark:text-blue-400',
  DEBUG: 'text-gray-400 dark:text-gray-500',
  TRACE: 'text-gray-300 dark:text-gray-600',
};

function detectLineLevel(line: string): string | null {
  const upper = line.toUpperCase();
  for (const level of ['FATAL', 'ERROR', 'WARN', 'INFO', 'DEBUG', 'TRACE']) {
    if (upper.includes(level)) return level;
  }
  return null;
}

function highlightText(line: string, pattern: RegExp | null): React.ReactNode {
  if (!pattern) return line;
  const parts = line.split(pattern);
  if (parts.length === 1) return line;
  const result: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  const flags = pattern.flags.includes('g') ? pattern.flags : pattern.flags + 'g';
  const regex = new RegExp(pattern.source, flags);
  while ((match = regex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      result.push(line.slice(lastIndex, match.index));
    }
    result.push(
      <mark key={match.index} className="bg-yellow-300/50 dark:bg-yellow-500/30 rounded-sm">
        {match[0]}
      </mark>,
    );
    lastIndex = match.index + match[0].length;
    if (match[0].length === 0) break;
  }
  if (lastIndex < line.length) {
    result.push(line.slice(lastIndex));
  }
  return result.length > 0 ? <>{result}</> : line;
}

export function VirtualLineList({
  lines,
  lineHeight = 22,
  overscan = 10,
  highlightPattern,
  highlightLine,
  tailMode = false,
  className = '',
}: VirtualLineListProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [containerHeight, setContainerHeight] = useState(600);

  // Measure container on mount + resize
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerHeight(entry.contentRect.height);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Auto-scroll to bottom in tail mode
  useEffect(() => {
    if (tailMode && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [tailMode, lines.length]);

  // Scroll to highlightLine
  useEffect(() => {
    if (highlightLine != null && containerRef.current && highlightLine >= 0) {
      const targetTop = highlightLine * lineHeight - containerHeight / 2;
      containerRef.current.scrollTop = Math.max(0, targetTop);
    }
  }, [highlightLine, lineHeight, containerHeight]);

  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    setScrollTop(e.currentTarget.scrollTop);
  }, []);

  // Calculate visible range
  const totalHeight = lines.length * lineHeight;
  const startIdx = Math.max(0, Math.floor(scrollTop / lineHeight) - overscan);
  const visibleCount = Math.ceil(containerHeight / lineHeight) + overscan * 2;
  const endIdx = Math.min(lines.length, startIdx + visibleCount);

  const visibleLines: Array<{ index: number; line: string }> = [];
  for (let i = startIdx; i < endIdx; i++) {
    visibleLines.push({ index: i, line: lines[i] ?? '' });
  }

  const pattern = highlightPattern ?? null;

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className={`overflow-auto bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded ${className}`}
      style={{ height: '100%', contain: 'strict' }}
    >
      <div style={{ height: `${totalHeight}px`, position: 'relative' }}>
        {visibleLines.map(({ index, line }) => {
          const level = detectLineLevel(line);
          const levelClass = level ? LEVEL_COLORS[level] ?? '' : '';
          const isSearchHit = pattern ? pattern.test(line) : false;

          return (
            <div
              key={index}
              className={`
                absolute left-0 w-full flex font-mono text-xs whitespace-pre
                hover:bg-gray-100 dark:hover:bg-gray-800/50
                ${levelClass}
                ${isSearchHit ? 'bg-yellow-100 dark:bg-yellow-900/20' : ''}
                ${highlightLine === index ? 'bg-orange-200 dark:bg-orange-900/30 ring-1 ring-orange-400' : ''}
              `}
              style={{ top: index * lineHeight, height: lineHeight }}
            >
              <span className="inline-block w-16 pr-2 text-right text-gray-400 dark:text-gray-600 select-none flex-shrink-0 border-r border-gray-200 dark:border-gray-700 mr-2">
                {index + 1}
              </span>
              <span className="flex-1 overflow-hidden text-ellipsis">
                {highlightText(line, pattern)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
