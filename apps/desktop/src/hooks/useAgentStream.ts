/**
 * useAgentStream —— React hook：订阅 Agent SSE 事件流，
 * 将事件路由到 chatStore / traceStore / terminal。
 *
 * 生命周期：组件挂载时订阅，卸载时自动取消。
 *
 * Phase 12 V1：trace + log 事件同时写两个目标
 *   1. traceStore —— 右侧 trace 面板（保留）
 *   2. chatStore.appendExecution —— 主对话内联 Codex/Claude 风格 step 块（**新**）
 */
import { useEffect } from 'react';
import { subscribeAgentStream } from '@/streams/agentStream';
import { useChatStore } from '@/store/chatStore';
import { useThinkingStore } from '@/store/thinkingStore';
import { useTraceStore } from '@/store/traceStore';
import { isMockText } from '@/lib/mockFilter';

export function useAgentStream(): void {
  const update = useChatStore((s) => s.update);
  const appendChat = useChatStore((s) => s.append);
  const appendExec = useChatStore((s) => s.appendExecution);
  const setBusy = useChatStore((s) => s.setBusy);
  const appendTrace = useTraceStore((s) => s.append);
  const setThinkingSession = useThinkingStore((s) => s.setSessionId);
  const refreshThinking = useThinkingStore((s) => s.refresh);

  useEffect(() => {
    return subscribeAgentStream((evt) => {
      switch (evt.kind) {
        case 'message': {
          // 消息事件：检查是否已存在（更新）还是新消息（追加）
          if (evt.message?.id) {
            const state = useChatStore.getState();
            const tab = state.tabs.find((t) => t.id === state.activeTabId);
            const exists = tab?.messages.some((m) => m.id === evt.message.id) ?? false;
            if (exists) {
              update(evt.message.id, evt.message);
            } else {
              appendChat(evt.message);
            }
          }
          break;
        }

        case 'tool_call':
          appendExec({
            id: evt.id ?? `tool-${Date.now()}`,
            role: 'system',
            kind: 'execution',
            category: 'tool_call',
            content: evt.call?.name ?? 'tool_call',
            status: 'running',
          });
          break;

        case 'tool_result':
          appendExec({
            id: `${evt.id}-result`,
            role: 'system',
            kind: 'execution',
            category: 'tool_call',
            content: evt.result
              ? `${evt.result.name} ${evt.result.ok ? '✓' : '✗'}`
              : 'tool_result',
            status: evt.result?.ok === false ? 'err' : 'ok',
          });
          break;

        case 'trace':
          if (evt.step) {
            if (isMockText(evt.step.summary) || isMockText(evt.step.error)) {
              break;
            }
            appendTrace(evt.step);
            // Phase 16：trace 事件携带 runId → 绑定思维链会话（后端不区分模式一律记录）
            if (evt.runId) {
              setThinkingSession(evt.runId);
            }
            appendExec({
              id: evt.step.id ?? `trace-${Date.now()}`,
              role: 'system',
              kind: 'execution',
              category: evt.step.node ?? 'node',
              content: evt.step.summary ?? evt.step.node ?? 'step',
              // TraceStep.status: 'ok' | 'fail' | 'running' | 'skipped' → 映射到执行链路三态
              status: (evt.step.status === 'fail' || evt.step.status === 'skipped')
                ? 'err'
                : (evt.step.status === 'running' ? 'running' : 'ok'),
              ...(evt.step.durationMs != null ? { latencyMs: evt.step.durationMs } : {}),
            });
          }
          break;

        case 'log':
          // 来自 codenav.explain / 其他主动 log；行内容已含 category 前缀
          if (isMockText((evt as { line?: string }).line)) {
            break;
          }
          appendExec({
            id: `log-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            role: 'system',
            kind: 'execution',
            category: 'log',
            content: typeof (evt as { line?: string }).line === 'string'
              ? (evt as { line?: string }).line!
              : JSON.stringify(evt),
            status: 'ok',
          });
          break;

        case 'approval':
          if (evt.approval) {
            appendChat({
              id: evt.approval.id,
              role: 'system',
              content: '等待审批中...',
              pendingApproval: evt.approval,
            });
          }
          break;

        // ---- Phase 18 双框架 ----
        case 'mode_routed': {
          // 路由徽标：[编程] / [工作] / [混合]；偏离模式默认时附声明文案
          const badge = evt.routing === 'coding' ? '编程' : evt.routing === 'work' ? '工作' : '混合';
          appendExec({
            id: `routing-${Date.now()}`,
            role: 'system',
            kind: 'execution',
            category: 'routing',
            content: evt.overridden && evt.declaration
              ? `[${badge}] ${evt.declaration}`
              : `[${badge}流程]`,
            status: 'ok',
          });
          break;
        }

        case 'repair_attempt':
          appendExec({
            id: `repair-${Date.now()}`,
            role: 'system',
            kind: 'execution',
            category: 'repair',
            content: `Auto-Repair 第 ${evt.attempt}/${evt.maxAttempts} 次修复中…${
              evt.errorSummary ? `（${evt.errorSummary.slice(0, 80)}）` : ''
            }`,
            status: 'running',
          });
          break;

        case 'auto_decision':
          appendExec({
            id: `auto-${Date.now()}`,
            role: 'system',
            kind: 'execution',
            category: 'auto_decision',
            content: `自动模式决策：${evt.option ? `按推荐项「${evt.option}」执行` : evt.reason}（已记审计）`,
            status: 'ok',
          });
          break;

        case 'done':
          // 流正常结束 → 解除 busy 状态 + 思维链最终刷新（补齐末批步骤）
          setBusy(false);
          void refreshThinking();
          break;

        case 'error':
          // 流异常终止 → 显示错误并解除 busy
          setBusy(false);
          appendChat({
            id: `error-${Date.now()}`,
            role: 'system',
            content: `运行出错：${(evt as { message?: string }).message ?? '未知错误'}`,
          });
          break;
      }
    });
  }, [appendChat, appendExec, appendTrace, update, setBusy, setThinkingSession, refreshThinking]);
}
