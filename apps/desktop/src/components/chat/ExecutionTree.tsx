/**
 * ExecutionTree —— 连续执行步骤的树形合并块（2026-08-27）。
 *
 * 背景：长任务一次跑十几个工具调用，平铺的执行块刷屏且无法整体把握。
 * 合并规则：连续的 kind='execution' 消息折叠为一棵树——摘要行（步数 /
 * 完成度 / 总耗时）+ 缩进子项；审批卡 / 追问 / 终答等非执行消息天然把
 * 树切断分段。
 *
 * 交互：
 *   - 有步骤进行中 → 自动展开；全部完成 → 自动收起（用户手动切换后尊重手动状态）
 *   - 点子项中文动作 → 右侧思维链精确定位（携带 occurrence，同名多次调用不串）
 */
import { useEffect, useRef, useState } from 'react';
import type { ChatMessage } from '@eaide/shared-protocol';
import { ExecutionRow } from './ExecutionBlock';

export interface ExecutionTreeItem {
  message: ChatMessage;
  /** 同名工具/节点的全局第 N 次（1 基），供右侧思维链精确定位 */
  occurrence?: number;
}

interface ExecutionTreeProps {
  items: ExecutionTreeItem[];
}

/** 同类合并后的树行（2026-08-27 用户反馈：22 条相同的「执行命令」刷屏） */
interface CompressedRow {
  rowKey: string;
  /** 代表行：展示与点击跳转都基于它（保留最早 occurrence） */
  head: ExecutionTreeItem;
  count: number;
  status: 'running' | 'ok' | 'err';
  totalMs: number;
}

/**
 * 同类合并：连续的同名工具调用行（category=tool_call 且工具名相同）折叠为一行，
 * 显示 ×N 徽标；其他类型步骤（节点/修复/日志）原样保留。
 * 聚合状态：任一 running → running；否则任一 err → err；否则 ok。
 * 注：合并行的输出面板只展示首次调用（callId 取代表行），属有意简化。
 */
export function compressSameType(items: ExecutionTreeItem[]): CompressedRow[] {
  const rows: CompressedRow[] = [];
  for (const item of items) {
    const mergeKey =
      item.message.category === 'tool_call'
        ? `tool:${(item.message.content ?? '').trim()}`
        : null;
    const last = rows[rows.length - 1];
    if (mergeKey && last && last.rowKey === mergeKey) {
      last.count += 1;
      last.totalMs += item.message.latencyMs ?? 0;
      const s = item.message.status;
      if (s === 'running') last.status = 'running';
      else if (s === 'err' && last.status !== 'running') last.status = 'err';
    } else {
      const s = item.message.status;
      rows.push({
        rowKey: mergeKey ?? `solo:${item.message.id}`,
        head: item,
        count: 1,
        status: s === 'running' ? 'running' : s === 'err' ? 'err' : 'ok',
        totalMs: item.message.latencyMs ?? 0,
      });
    }
  }
  return rows;
}

export function ExecutionTree({ items }: ExecutionTreeProps): JSX.Element {
  const runningCount = items.filter((i) => i.message.status === 'running').length;
  const errCount = items.filter((i) => i.message.status === 'err').length;
  const doneCount = items.length - runningCount;
  const totalMs = items.reduce(
    (acc, i) => acc + (i.message.latencyMs ?? 0),
    0,
  );
  const allDone = runningCount === 0;

  // 展开态：进行中自动展开；全部完成自动收起一次；用户手动切换后尊重手动状态，
  // 直到再次出现进行中步骤（新一轮执行）重置回自动跟随。
  const [open, setOpen] = useState(!allDone);
  const manual = useRef(false);
  useEffect(() => {
    if (runningCount > 0) {
      manual.current = false;
      setOpen(true);
    } else if (!manual.current) {
      setOpen(false);
    }
  }, [runningCount]);

  const toggle = (): void => {
    manual.current = true;
    setOpen((v) => !v);
  };

  const summaryColor = errCount > 0 ? '#dc2626' : allDone ? '#059669' : '#0891b2';
  const summary =
    errCount > 0
      ? `${items.length} 步 · ${errCount} 失败`
      : allDone
        ? `${items.length} 步 · 全部完成`
        : `${doneCount}/${items.length} 步`;

  return (
    <div
      className="my-1 rounded-lg text-2xs"
      style={{
        backgroundColor: '#fafaf9',
        border: '1px solid #f0efed',
        borderLeft: `3px solid ${summaryColor}`,
      }}
    >
      {/* 摘要行：点击整行展开/收起 */}
      <button
        type="button"
        onClick={toggle}
        title={open ? '收起执行步骤' : '展开执行步骤'}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left"
        style={{ color: '#202124' }}
      >
        {runningCount > 0 ? (
          <span
            className="animate-spin-ring flex-shrink-0 rounded-full"
            style={{
              width: 11,
              height: 11,
              border: '2px solid #d1d5db',
              borderTopColor: summaryColor,
            }}
            title="进行中"
          />
        ) : (
          <span
            className="flex-shrink-0 font-bold"
            style={{ color: summaryColor, width: 14 }}
          >
            {errCount > 0 ? '✗' : '✓'}
          </span>
        )}
        <span className="flex-shrink-0 font-semibold" style={{ color: summaryColor }}>
          执行过程
        </span>
        <span className="flex-1 truncate" style={{ color: '#6b7280' }}>
          {summary}
        </span>
        {totalMs > 0 && allDone && (
          <span className="flex-shrink-0 font-mono" style={{ color: '#9ca3af' }}>
            · {(totalMs / 1000).toFixed(1)}s
          </span>
        )}
        <span className="flex-shrink-0" style={{ color: '#9ca3af' }}>
          {open ? '▾' : '▸'}
        </span>
      </button>

      {/* 子项：缩进 + 左侧树形引导线；连续同名工具已压缩为一行（×N 徽标） */}
      {open && (
        <div
          className="border-t pb-1"
          style={{ borderColor: '#f0efed' }}
        >
          <div className="ml-[17px]" style={{ borderLeft: '1px solid #e7e5e4' }}>
            {compressSameType(items).map((row) => (
              <div
                key={row.head.message.id}
                className="relative"
              >
                {/* 树形连接横线 */}
                <span
                  className="absolute left-0 top-1/2 w-2"
                  style={{ borderTop: '1px solid #e7e5e4' }}
                  aria-hidden="true"
                />
                <div className="pl-2">
                  <ExecutionRow
                    message={{
                      ...row.head.message,
                      // 聚合态覆盖：行内任一在跑即转圈，任一失败即标红；
                      // 耗时展示合并行总耗时（无耗时数据时保持原样不渲染）
                      status: row.status,
                      ...(row.totalMs > 0 ? { latencyMs: row.totalMs } : {}),
                    }}
                    {...(row.head.occurrence != null ? { occurrence: row.head.occurrence } : {})}
                    repeatCount={row.count}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
