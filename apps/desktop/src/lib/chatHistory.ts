/**
 * chatHistory —— 输入框 ↑/↓ 历史记录快捷输入的数据准备（2026-08-26）。
 *
 * 与终端/REPL 习惯一致：只取当前页签（会话）的用户消息做快捷回填源。
 */
import type { ChatMessage } from '@eaide/shared-protocol';

/**
 * 提取会话内的用户历史输入：仅 role=user、过滤空白、连续去重，
 * 返回时间顺序（旧 → 新）。调用方从尾部倒序回填。
 */
export function buildUserHistory(messages: ChatMessage[]): string[] {
  const out: string[] = [];
  for (const m of messages) {
    if (m.role !== 'user' || !m.content) continue;
    const t = m.content.trim();
    if (!t) continue;
    if (out[out.length - 1] === t) continue; // 连续重复只留一条
    out.push(t);
  }
  return out;
}
