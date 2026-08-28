/**
 * TodoCard —— 任务进度待办卡片（2026-08-25，竖向布局 2026-08-28）。
 *
 * 数据流：模型调 update_todos 伪工具 → 后端经 trace 通道（step.todos）下发 →
 * useAgentStream 按固定消息 id 原地更新（chatStore.upsertTodo）→ 本卡片实时
 * 刷新进度。同一任务始终只有一张卡（原地覆盖，不新增气泡）。
 *
 * 布局：竖向三段式（标题行 / 进度条 / 条目列表），宽度自适应容器，
 * 兼容左侧窄面板（任务计划页签）与宽区域展示。
 *
 * 前端等待规范：in_progress 项带细环 spinner（.animate-spin-ring），
 * 用户能实时看到「正在做哪一步」。
 */
import { useMemo } from 'react';
import type { TodoItem } from '@eaide/shared-protocol';

interface Props {
  /** TodoItem[] 的 JSON 字符串（ChatMessage.content） */
  itemsJson: string;
}

const STATUS_ICON: Record<TodoItem['status'], string> = {
  pending: '○',
  in_progress: '',
  done: '✓',
};

const STATUS_COLOR: Record<TodoItem['status'], string> = {
  pending: '#9ca3af',
  in_progress: '#0b6bcb',
  done: '#059669',
};

export function TodoCard({ itemsJson }: Props): JSX.Element {
  const items = useMemo<TodoItem[]>(() => {
    try {
      const parsed: unknown = JSON.parse(itemsJson);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter(
        (t): t is TodoItem =>
          !!t && typeof t === 'object' && typeof (t as TodoItem).content === 'string',
      );
    } catch {
      return [];
    }
  }, [itemsJson]);

  if (items.length === 0) return <></>;
  const done = items.filter((t) => t.status === 'done').length;
  const pct = Math.round((done / items.length) * 100);

  return (
    <div
      className="my-1 rounded-lg border p-3"
      style={{ borderColor: '#e5e7eb', backgroundColor: '#f9fafb' }}
      role="status"
      aria-live="polite"
    >
      {/* 标题行：图标 + 完成计数（竖向下不再和进度条挤同一行） */}
      <div className="mb-1.5 flex items-center gap-2">
        <span className="text-xs font-semibold" style={{ color: '#374151' }}>
          📋 任务进度
        </span>
        <span className="text-2xs" style={{ color: '#6b7280' }}>
          {done}/{items.length}
        </span>
        <span className="ml-auto text-2xs font-medium" style={{ color: '#059669' }}>
          {pct}%
        </span>
      </div>
      {/* 进度条：独占一行，宽度随面板拉伸 */}
      <div
        className="mb-2 h-1.5 w-full overflow-hidden rounded-full"
        style={{ backgroundColor: '#e5e7eb' }}
      >
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ width: `${pct}%`, backgroundColor: '#059669' }}
        />
      </div>
      <ul className="space-y-1.5">
        {items.map((t, i) => (
          <li key={`${i}-${t.content.slice(0, 24)}`} className="flex items-center gap-2 text-xs">
            {t.status === 'in_progress' ? (
              <span
                className="animate-spin-ring flex-shrink-0"
                style={{ width: 12, height: 12, borderColor: STATUS_COLOR.in_progress }}
                aria-hidden="true"
              />
            ) : (
              <span
                className="flex-shrink-0 font-bold"
                style={{ color: STATUS_COLOR[t.status] ?? '#9ca3af' }}
                aria-hidden="true"
              >
                {STATUS_ICON[t.status] ?? '○'}
              </span>
            )}
            <span
              className="min-w-0 flex-1 break-words"
              style={{
                color: t.status === 'done' ? '#6b7280' : '#1f2937',
                textDecoration: t.status === 'done' ? 'line-through' : 'none',
              }}
            >
              {t.content}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
