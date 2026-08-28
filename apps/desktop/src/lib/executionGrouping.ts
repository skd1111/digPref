/**
 * 连续执行步骤分组（2026-08-27 树形合并）。
 *
 * 多条连续的 kind='execution' 消息折叠为一棵执行树（ExecutionTree），
 * 单条保持原样（ExecutionBlock）；审批卡 / 追问 / 终答等非执行消息
 * 天然把树切断分段。
 *
 * occurrence：同名工具/节点的全局第 N 次（1 基，从早到晚）——点击跳转
 * 时带给右侧思维链精确定位（同名工具多次调用不串）。
 *
 * 纯函数设计便于单测；CenterChatFlow 负责渲染接线。
 */
import type { ChatMessage } from '@eaide/shared-protocol';
import { jumpQueryFor } from '@/components/chat/ExecutionBlock';

export interface ExecutionGroupItem {
  message: ChatMessage;
  /** 同名工具/节点的全局第 N 次（1 基） */
  occurrence: number;
}

export type ExecutionRenderItem =
  | { type: 'msg'; m: ChatMessage; occurrence?: number }
  | { type: 'tree'; key: string; items: ExecutionGroupItem[] };

export function isExecutionMessage(m: ChatMessage): boolean {
  return m.role === 'system' && m.kind === 'execution';
}

export function groupExecutionSteps(messages: ChatMessage[]): ExecutionRenderItem[] {
  const seen: Record<string, number> = {};
  const items: ExecutionRenderItem[] = [];
  let group: ExecutionGroupItem[] = [];
  const flush = (): void => {
    if (group.length === 0) return;
    if (group.length === 1) {
      const g = group[0];
      items.push({ type: 'msg', m: g.message, occurrence: g.occurrence });
    } else {
      items.push({ type: 'tree', key: group[0].message.id, items: group });
    }
    group = [];
  };
  for (const m of messages) {
    if (isExecutionMessage(m)) {
      const key = jumpQueryFor(m);
      seen[key] = (seen[key] ?? 0) + 1;
      group.push({ message: m, occurrence: seen[key] });
    } else {
      flush();
      items.push({ type: 'msg', m });
    }
  }
  flush();
  return items;
}
