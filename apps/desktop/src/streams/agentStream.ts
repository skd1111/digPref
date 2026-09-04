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
    // 2026-08-26：内置工具开始事件（把「思考中」细化为「工具调用中：某动作」）
    listen<AgentStreamEvent>(EVT.BUILTIN_TOOL_STARTED, (e) => handler(e.payload)),
    // Phase 2D V0：skill 路由命中（2026-08-26 起记录 lastSkillId 供追问轮继承）
    listen<AgentStreamEvent>(EVT.AGENT_SKILL_MATCHED, (e) => handler(e.payload)),
    // 执行过程可视化（Claude Code 式）：run 开始 / 工具进度 / shell 流式输出 / 写前 Diff 预览
    listen<AgentStreamEvent>(EVT.AGENT_RUN_STARTED, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_TOOL_PROGRESS, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_SHELL_CHUNK, (e) => handler(e.payload)),
    // 回答逐字流式（2026-09-03）：responder 终答 token 增量
    listen<AgentStreamEvent>(EVT.AGENT_ANSWER_DELTA, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_FILE_WRITE_PREVIEW, (e) => handler(e.payload)),
    // 生命周期通道：流结束和错误
    listen<AgentStreamEvent>(EVT.AGENT_DONE, (e) => handler(e.payload)),
    listen<AgentStreamEvent>(EVT.AGENT_ERROR, (e) => handler(e.payload)),
    // 流保活心跳（BUGFIX #161）：看门狗据此感知流存活，静默超阈解锁防永久卡死
    listen<AgentStreamEvent>(EVT.AGENT_HEARTBEAT, (e) => handler(e.payload)),
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
