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
import { ipc } from '@/ipc/invoke';
import { useChatStore } from '@/store/chatStore';
import { useThinkingStore } from '@/store/thinkingStore';
import { useTraceStore } from '@/store/traceStore';
import { isMockText } from '@/lib/mockFilter';

/**
 * 内部调度节点黑名单：只进右侧思维链 / 审计，不注入主对话执行链路。
 * 主对话只保留对用户有意义的步骤：工具调用、Auto-Repair、自动决策、日志。
 */
const CHAT_HIDDEN_TRACE_NODES = new Set([
  'mode_router',
  'intent',
  'local_intent',
  'rag_retrieve',
  'planner',
  'decompose',
  'tool_orchestrator',
  'hitl_gate',
  'responder',
  'vision_understand',
]);

/**
 * 知识检索类动作判定（rag/retrieve，2026-08-17）：只进思维链 / 审计，
 * 不注入主对话（用户不需要看到「搜索完成“检索知识库”」卡片）。
 */
function isKnowledgeRetrieval(name: string | undefined): boolean {
  if (!name) return false;
  return /rag|retrieve/.test(name.toLowerCase());
}

/**
 * 搜索/检索类工具判定（2026-08-10）：命中后不走普通执行链路块，
 * 而是在对话流中渲染 aicss 风格搜索卡片（kind='search'）。
 * 知识检索类（rag/retrieve）除外：2026-08-17 起不再在对话展示。
 */
function isSearchTool(name: string | undefined): boolean {
  if (!name) return false;
  if (isKnowledgeRetrieval(name)) return false;
  const n = name.toLowerCase();
  return /search|grep|web/.test(n);
}

/** 搜索类工具名 → 用户可读的查询描述（搜索卡片标题） */
function searchLabel(name: string | undefined): string {
  const n = (name ?? '').toLowerCase();
  if (n.includes('web')) return '联网搜索';
  if (n.includes('grep')) return '搜索代码内容';
  if (n.includes('symbol')) return '检索代码符号';
  return '搜索中';
}

/** 写文件类 builtin 工具名（2026-08-19 改动文件累积用） */
const WRITE_FILE_TOOLS = new Set(['write_file', 'edit_file']);

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

        case 'tool_call': {
          const toolName = evt.call?.name ?? 'tool_call';
          // 知识检索类工具不进对话（2026-08-17，只留思维链）
          if (isKnowledgeRetrieval(toolName)) break;
          if (isSearchTool(toolName)) {
            // 搜索类工具 → aicss 风格搜索卡片（取代普通执行链路块）
            appendChat({
              id: evt.id ?? `search-${Date.now()}`,
              role: 'system',
              kind: 'search',
              content: searchLabel(toolName),
              category: toolName,
              status: 'running',
            });
            break;
          }
          appendExec({
            id: evt.id ?? `tool-${Date.now()}`,
            role: 'system',
            kind: 'execution',
            category: 'tool_call',
            content: toolName,
            status: 'running',
          });
          break;
        }

        case 'tool_result': {
          const resultName = evt.result?.name ?? '';
          // 知识检索类工具结果不进对话（与 tool_call 侧对齐）
          if (isKnowledgeRetrieval(resultName)) break;
          if (isSearchTool(resultName)) {
            // 更新对应搜索卡片为完成态（id 与 tool_call 一致）
            update(evt.id ?? '', {
              status: evt.result?.ok === false ? 'err' : 'ok',
            });
            break;
          }
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
        }

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
            // 知识检索节点不在对话展示（2026-08-17）：rag_retrieve 已在
            // CHAT_HIDDEN_TRACE_NODES 黑名单，落入下方隐藏分支只进思维链
            // 2026-08-04：内部调度节点（路由 / 检索 / 分解 / 终答等）只进思维链，不进主对话
            if (CHAT_HIDDEN_TRACE_NODES.has(evt.step.node ?? '')) {
              break;
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
          // 2026-08-04：路由信息只在思维链 / 审计留痕，不再注入主对话
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

        case 'builtin_tool_done': {
          // 2026-08-19：write_file / edit_file 成功 → 累积改动路径（result_meta.path），
          // done 时汇总成 changed_files 卡片展示
          if (
            evt.tool_name &&
            WRITE_FILE_TOOLS.has(evt.tool_name) &&
            evt.ok
          ) {
            const p =
              typeof evt.result_meta?.path === 'string' ? evt.result_meta.path : '';
            if (p) {
              useChatStore.getState().addChangedFile(p);
            }
          }
          break;
        }

        case 'done': {
          // 流正常结束 → 解除 busy 状态 + 思维链最终刷新（补齐末批步骤）
          setBusy(false);
          const st = useChatStore.getState();
          st.setRunId(null);
          // 整轮耗时统计（2026-08-07）
          if (st.runStartTs != null) {
            st.setLastRunMs(Date.now() - st.runStartTs);
            st.setRunStartTs(null);
          }
          // 2026-08-19：任务结束汇总 —— 本轮 write_file / edit_file 改动的文件
          // 以可点击 changed_files 卡片追入对话（点击在 Monaco 打开）
          if (st.changedFiles.length > 0) {
            appendChat({
              id: `changed-files-${Date.now()}`,
              role: 'system',
              kind: 'changed_files',
              content: JSON.stringify(st.changedFiles),
              status: 'ok',
            });
            st.clearChangedFiles();
          }
          // sessions 归档（2026-08-07，best-effort）：把最后一条 assistant 回复写进后端
          const tab = st.tabs.find((t) => t.id === st.activeTabId);
          if (tab?.backendSessionId) {
            for (let i = tab.messages.length - 1; i >= 0; i--) {
              const m = tab.messages[i];
              if (m.role === 'assistant' && m.content) {
                void ipc
                  .sessionsAppendMessage(tab.backendSessionId, {
                    role: 'assistant',
                    content: m.content,
                  })
                  .catch(() => undefined);
                break;
              }
            }
          }
          void refreshThinking();
          break;
        }

        case 'error':
          // 流异常终止 → 显示错误并解除 busy；kind='error' 让前端渲染「重试」按钮
          setBusy(false);
          useChatStore.getState().setRunId(null);
          // 2026-08-19：异常终止也汇总已发生的改动（避免累积泄漏到下一轮）
          {
            const stErr = useChatStore.getState();
            if (stErr.changedFiles.length > 0) {
              appendChat({
                id: `changed-files-${Date.now()}`,
                role: 'system',
                kind: 'changed_files',
                content: JSON.stringify(stErr.changedFiles),
                status: 'ok',
              });
              stErr.clearChangedFiles();
            }
          }
          appendChat({
            id: `error-${Date.now()}`,
            role: 'system',
            kind: 'error',
            content: `运行出错：${(evt as { message?: string }).message ?? '未知错误'}`,
          });
          break;
      }
    });
  }, [appendChat, appendExec, appendTrace, update, setBusy, setThinkingSession, refreshThinking]);
}
