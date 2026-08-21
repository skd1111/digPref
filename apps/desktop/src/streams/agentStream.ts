/**
 * agentStream —— 消费 Rust SSE 桥发出的 Tauri 事件流。
 *
 * 为什么通过 Rust 而不是直接从 WebView 连 SSE：
 *   - WebView 无法跨 Tauri CSP 边界直连 127.0.0.1:8765
 *   - 通过 Rust 代理可以过滤/脱敏敏感 payload 再交前端
 *   - 统一的事件总线便于调试和日志
 *
 * 协议：SSE 事件通过 `event:` 字段区分类型，Rust 按 channel 映射分发。
 */
import { listen, EVT } from '@/ipc/events';
import type { AgentStreamEvent } from '@/ipc/types';

type Handler = (evt: AgentStreamEvent) => void;

/**
 * 订阅 Agent SSE 事件流（5 个数据通道 + 2 个生命周期通道）。
 *
 * 返回取消订阅函数。组件应在 useEffect cleanup 中调用。
 * 修复了竞态条件：使用 AbortController 模式确保异步 subscribe 的
 * 取消与同步返回的 cleanup 函数正确协调。
 */
export function subscribeAgentStream(handler: Handler): () => void {
  const unsubFns: Array<() => void> = [];
  let cancelled = false;

  // 使用 Promise.allSettled 确保即使某个通道失败也不影响其他
  void Promise.allSettled([
    listen<AgentStreamEvent>(EVT.AGENT_MESSAGE, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_TOOL_CALL, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_TOOL_RESULT, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_TRACE, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_APPROVAL_REQUEST, (e) => handler(e.payload)),
    // Phase 18 双框架：路由结果 / Auto-Repair 进度 / 自动模式决策
    listen<AgentStreamEvent>(EVT.AGENT_MODE_ROUTED, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_REPAIR_ATTEMPT, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_AUTO_DECISION, (e) => handler(e.payload)),
    // Phase 1B V1：内置工具完成事件（2026-08-19 起用于累积改动文件清单）
    listen<AgentStreamEvent>(EVT.BUILTIN_TOOL_DONE, (e) => handler(e.payload)),
    // 生命周期通道：流结束和错误
    listen<AgentStreamEvent>(EVT.AGENT_DONE, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_ERROR, (e) => handler(e.payload)),
  ]).then((results) => {
    if (cancelled) {
      // 在 subscribe 完成前已取消 → 立即取消所有
      for (const r of results) {
        if (r.status === 'fulfilled') r.value();
      }
    } else {
      // 收集 unsubscribe 函数供后续清理
      for (const r of results) {
        if (r.status === 'fulfilled') unsubFns.push(r.value);
      }
    }
  });

  return () => {
    cancelled = true;
    for (const u of unsubFns) u();
  };
}
