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
import { toolActionLabel } from '@/components/chat/ExecutionBlock';
import {
  WATCHDOG_TICK_MS,
  WATCHDOG_SILENCE_MS,
  noteStreamEvent,
  runWatchdogTick,
} from '@/lib/streamWatchdog';

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

/** 创建类产物工具名（2026-08-26 交付产物累积用）：新建文件，区别于 edit 类修改 */
const ARTIFACT_TOOLS = new Set(['write_file', 'office_create', 'word_generate', 'excel_export', 'pdf_merge']);

/** 不发「做完了」进度回执的伪工具（待办卡/追问已各自有交互面，避免刷屏） */
const QUIET_STEP_TOOLS = new Set(['update_todos', 'ask_user', 'datetime_now', 'date_parse']);

export function useAgentStream(): void {
  const update = useChatStore((s) => s.update);
  const appendTrace = useTraceStore((s) => s.append);
  const setThinkingSession = useThinkingStore((s) => s.setSessionId);
  const refreshThinking = useThinkingStore((s) => s.refresh);

  useEffect(() => {
    /** 多会话并发（2026-08-26）：事件按 runId→页签路由；无 runId 回退激活页签 */
    const tabFor = (runId?: string): string => {
      const s = useChatStore.getState();
      if (runId) {
        const tabId = s.runTabMap[runId];
        if (tabId) return tabId;
      }
      return s.activeTabId;
    };

    const unsubscribe = subscribeAgentStream((evt) => {
      // BUGFIX #161：任一事件到达即刷新流存活时间戳（看门狗据此判静默）
      noteStreamEvent();
      switch (evt.kind) {
        case 'heartbeat': {
          // 纯保活心跳：时间戳已在入口刷新，无其它副作用（防流静默断连卡死）
          break;
        }

        case 'message': {
          // 消息事件：按 run 归属页签路由（并发时两个会话的流不串台）
          if (evt.message?.id) {
            const state = useChatStore.getState();
            const tabId = tabFor(evt.message.runId);
            const tab = state.tabs.find((t) => t.id === tabId);
            const exists = tab?.messages.some((m) => m.id === evt.message.id) ?? false;
            if (exists) {
              update(evt.message.id, evt.message);
            } else {
              state.appendToTab(tabId, evt.message);
            }
          }
          break;
        }

        case 'tool_call': {
          const toolName = evt.call?.name ?? 'tool_call';
          // 知识检索类工具不进对话（2026-08-17，只留思维链）
          if (isKnowledgeRetrieval(toolName)) break;
          const tabId = tabFor(evt.runId);
          const st = useChatStore.getState();
          // 配对键（根治 BUGFIX #164）：用后端下发的 callId 派生消息 id，
          // tool_result 到达时按同一 id 精确翻牌。重试 / HITL 恢复重跑同一步会
          // 复用同一 callId → 原地更新那张卡，不再堆出第二张。
          const callId = evt.callId ?? evt.call?.call_id;
          const msgId = callId ? `tool-${callId}` : (evt.id ?? `tool-${Date.now()}`);
          // 同 id 已存在（重试 / 审批后重跑）→ 置回 running，不重复追加
          const existing = st.tabs.find((t) => t.id === tabId)?.messages ?? [];
          if (callId && existing.some((m) => m.id === msgId)) {
            update(msgId, { status: 'running', content: toolName });
            break;
          }
          if (isSearchTool(toolName)) {
            // 搜索类工具 → aicss 风格搜索卡片（取代普通执行链路块）
            st.appendToTab(tabId, {
              id: msgId,
              role: 'system',
              kind: 'search',
              content: searchLabel(toolName),
              category: toolName,
              status: 'running',
            });
            break;
          }
          st.appendExecutionToTab(tabId, {
            id: msgId,
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
          const ok = evt.result?.ok !== false;
          const stChat = useChatStore.getState();
          const tabId = tabFor(evt.runId);
          const tabMsgs = stChat.tabs.find((t) => t.id === tabId)?.messages ?? [];
          let flipped = false;
          // 首选：按 callId 精确配对（根治 BUGFIX #164）。后端 tool_call /
          // tool_result 现在共享 call_id，不再依赖工具名猜测。
          const callId = evt.callId ?? evt.result?.call_id;
          if (callId) {
            const target = `tool-${callId}`;
            if (tabMsgs.some((m) => m.id === target)) {
              update(target, { status: ok ? 'ok' : 'err' });
              break;
            }
          }
          // 兜底 A：按工具名找最近一条 running 同名调用块（兼容不带 callId 的旧后端）
          if (isSearchTool(resultName)) {
            // 搜索卡片 content 是展示标签（联网搜索…）而非工具名，按最近一张 running 搜索卡翻牌
            for (let i = tabMsgs.length - 1; i >= 0; i--) {
              const m = tabMsgs[i];
              if (m.kind === 'execution' && m.category === 'search' && m.status === 'running') {
                update(m.id, { status: ok ? 'ok' : 'err' });
                flipped = true;
                break;
              }
            }
            if (flipped) break;
          }
          if (resultName) {
            for (let i = tabMsgs.length - 1; i >= 0; i--) {
              const m = tabMsgs[i];
              if (
                m.kind === 'execution' &&
                m.category === 'tool_call' &&
                m.content === resultName &&
                m.status === 'running'
              ) {
                update(m.id, { status: ok ? 'ok' : 'err' });
                flipped = true;
                break;
              }
            }
            if (flipped) break;
          }
          // 兜底 B：连工具名都没有（旧后端 + 协议漂移）→ 翻最近一条 running 调用块。
          // 宁可翻错一张也不能留着永久转圈 —— 顺序执行下"最近一条"几乎总是对的。
          for (let i = tabMsgs.length - 1; i >= 0; i--) {
            const m = tabMsgs[i];
            if (m.kind === 'execution' && m.category === 'tool_call' && m.status === 'running') {
              update(m.id, { status: ok ? 'ok' : 'err' });
              flipped = true;
              break;
            }
          }
          if (flipped) break;
          // 找不到对应调用块（历史消息已清理等）→ 退回追加结果块的旧逻辑兜底
          stChat.appendExecutionToTab(tabId, {
            id: `${evt.id}-result`,
            role: 'system',
            kind: 'execution',
            category: 'tool_call',
            content: evt.result
              ? `${evt.result.name ?? 'tool_result'} ${evt.result.ok ? '✓' : '✗'}`
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
            // 任务进度待办（2026-08-25）：update_todos 伪工具经 trace 通道下发，
            // 按 runId 固定消息 id 原地更新卡片（同一任务始终一张卡）；
            // 不再作为普通执行链路块重复展示。
            // BUGFIX #169：写入 run 归属页签（非激活页签）—— 修复 A 会话跑任务时
            // 切到 B 会话，任务计划卡串到 B 且关掉 B 后彻底丢失。
            const todos = (evt.step as { todos?: unknown }).todos;
            if (evt.step.node === 'todo' && Array.isArray(todos) && todos.length > 0) {
              useChatStore.getState().upsertTodo(
                tabFor(evt.runId),
                `todo-${evt.runId ?? 'run'}`,
                JSON.stringify(todos),
              );
              break;
            }
            // 知识检索节点不在对话展示（2026-08-17）：rag_retrieve 已在
            // CHAT_HIDDEN_TRACE_NODES 黑名单，落入下方隐藏分支只进思维链
            // 2026-08-04：内部调度节点（路由 / 检索 / 分解 / 终答等）只进思维链，不进主对话
            if (CHAT_HIDDEN_TRACE_NODES.has(evt.step.node ?? '')) {
              break;
            }
            useChatStore.getState().appendExecutionToTab(tabFor(evt.runId), {
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
          useChatStore.getState().appendExecutionToTab(tabFor(undefined), {
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
            useChatStore.getState().appendToTab(tabFor(evt.runId), {
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
          useChatStore.getState().appendExecutionToTab(tabFor(evt.runId), {
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
          useChatStore.getState().appendExecutionToTab(tabFor(evt.runId), {
            id: `auto-${Date.now()}`,
            role: 'system',
            kind: 'execution',
            category: 'auto_decision',
            content: `自动模式决策：${evt.option ? `按推荐项「${evt.option}」执行` : evt.reason}（已记审计）`,
            status: 'ok',
          });
          break;

        case 'builtin_tool_started': {
          // 2026-08-26：工具开始执行 → 该 run 的指示器切到「工具调用中：某动作」
          if (evt.tool_name) {
            const runId = useChatStore.getState().attributeRun(evt.runId);
            if (runId) {
              useChatStore.getState().setRunPhaseForRun(runId, 'tool', toolActionLabel(evt.tool_name));
            }
          }
          break;
        }

        case 'builtin_tool_done': {
          // 工具执行结束 → 该 run 回到等模型返回（下一轮编排由模型决定）
          const stB = useChatStore.getState();
          const runIdB = stB.attributeRun(evt.runId);
          if (runIdB) {
            stB.setRunPhaseForRun(runIdB, 'model');
            // 2026-08-26：做完了就回执一句人性化进度（按 run 归属页签追加）
            if (
              evt.tool_name &&
              WRITE_FILE_TOOLS.has(evt.tool_name) &&
              evt.ok
            ) {
              const p =
                typeof evt.result_meta?.path === 'string' ? evt.result_meta.path : '';
              if (p) {
                stB.addChangedFile(runIdB, p);
              }
            }
            // 2026-08-26：创建类工具成功 → 累积交付产物（done 时决定是否弹验收清理卡）
            if (evt.tool_name && ARTIFACT_TOOLS.has(evt.tool_name) && evt.ok) {
              const p =
                typeof evt.result_meta?.path === 'string' ? evt.result_meta.path : '';
              if (p) {
                stB.addTaskArtifact(runIdB, p);
              }
            }
            // 进度回执：成功的真实工具才发；失败的交给既有错误链路，不重复刷屏
            if (evt.tool_name && evt.ok && !QUIET_STEP_TOOLS.has(evt.tool_name)) {
              stB.appendToTab(tabFor(evt.runId), {
                id: `step-done-${Date.now()}-${evt.tool_name}`,
                role: 'system',
                kind: 'execution',
                category: 'step_done',
                content: `做完了：${toolActionLabel(evt.tool_name)}，正在安排下一步…`,
                status: 'ok',
              });
            }
          }
          // 写前预览卡翻牌（执行过程可视化·阶段四）：工具终态决定对应预览卡成败；
          // 独立于 run 归属判断（无活跃 run 的兼容路径下预览卡也要能终结）；
          // HITL 拒绝走 builtin_tool_denied → 预览卡保持待审批态不误导。
          if (evt.call_id) {
            const previewId = `preview-${evt.call_id}`;
            const msgsP = stB.tabs.find((t) => t.id === tabFor(evt.runId))?.messages ?? [];
            if (msgsP.some((m) => m.id === previewId)) {
              update(previewId, { status: evt.ok ? 'ok' : 'err' });
            }
          }
          break;
        }

        // ---- 执行过程可视化（Claude Code 式，阶段三） ----
        case 'run_started': {
          // run 生命周期显式起点：页签忙碌态已由发送链路锁定（多会话并发按
          // runId 归属），这里不重复上锁，仅留作链路调试锚点。
          break;
        }

        case 'tool_progress': {
          // 长耗时工具阶段文案：写 store 供工具卡副标题实时刷新（不新增消息刷屏）
          const callIdP = evt.call_id ?? evt.callId;
          if (callIdP) {
            useChatStore.getState().setToolProgress(callIdP, evt.message ?? '');
          }
          break;
        }

        case 'shell_chunk': {
          // shell 流式输出：按 call_id 归并到工具卡输出面板；结束帧带 exit_code
          const callIdS = evt.call_id ?? evt.callId;
          if (!callIdS) break;
          const stS = useChatStore.getState();
          if (evt.chunk) stS.appendShellChunk(callIdS, evt.chunk);
          if (evt.exit_code != null) stS.closeShellStream(callIdS, evt.exit_code);
          break;
        }

        case 'file_write_preview': {
          // 写前 Diff 预览（阶段四）：审批暂停前先到达 —— 预览卡进执行链路，
          // diff 存 store 供 WritePreviewCard（内嵌 +/- 统计 + FullDiffModal 对比）。
          const callIdW = evt.call_id ?? evt.callId;
          const stW = useChatStore.getState();
          if (callIdW) stW.setWritePreview(callIdW, evt.path ?? '', evt.diff ?? '');
          stW.appendExecutionToTab(tabFor(evt.runId), {
            id: callIdW ? `preview-${callIdW}` : `preview-${Date.now()}`,
            role: 'system',
            kind: 'execution',
            category: 'file_write_preview',
            content: evt.path ?? '待写入文件',
            status: 'running',
          });
          break;
        }

        case 'skill_matched': {
          // 2026-08-26：记录最近命中的 skill，随下次发送透传（追问/修改轮继承）；
          // 2026-08-28：不再写 selectedSkill（输入框上方徽标已下线），改为往归属
          // 页签对话流追加一条执行步骤卡；同一 skill 固定 id 原地翻牌，追问轮不刷屏。
          if (evt.skill_id) {
            const st = useChatStore.getState();
            st.setLastSkillId(evt.skill_id);
            const tabId = tabFor(evt.runId);
            const msgId = `skill-matched-${evt.skill_id}`;
            const msgs = st.tabs.find((t) => t.id === tabId)?.messages ?? [];
            if (msgs.some((m) => m.id === msgId)) {
              update(msgId, { status: 'ok' });
            } else {
              st.appendExecutionToTab(tabId, {
                id: msgId,
                role: 'system',
                kind: 'execution',
                category: 'skill_matched',
                content: `已加载业务技能「${evt.skill_name ?? evt.skill_id}」`,
                status: 'ok',
              });
            }
          }
          break;
        }

        case 'done': {
          // 流正常结束 → 按 run 解除归属；多个会话可同时各自在跑（2026-08-26 并发）
          // BUGFIX #150：Rust 桥后端 done + stream_closed 补发双 done，幂等键从全局 runId
          // 改为 runTabMap 归属：只有还挂在归属表里的 run 才算首次结束。
          const st = useChatStore.getState();
          // 无 runId 的 done（旧后端/异常路径）回退归属推断（唯一活跃 run 或当前页签的 run）
          const runId = evt.runId || st.attributeRun(undefined);
          const tabId = tabFor(runId);
          const firstDone = st.isRunKnown(runId);
          // 整轮耗时统计（2026-08-07）：endRun 内部写 lastRunMs
          st.endRun(runId);
          if (firstDone) {
            const changed = st.changedFilesByRun[runId] ?? [];
            const artifacts = st.artifactsByRun[runId] ?? [];
            const hasTaskFiles = changed.length > 0 || artifacts.length > 0;
            // 2026-08-19：任务结束汇总 —— 本轮 write_file / edit_file 改动的文件
            // 以可点击 changed_files 卡片追入归属页签（点击在 Monaco 打开）
            if (changed.length > 0) {
              st.appendToTab(tabId, {
                id: `changed-files-${Date.now()}`,
                role: 'system',
                kind: 'changed_files',
                content: JSON.stringify(changed),
                status: 'ok',
              });
            }
            // 2026-08-26：验收清理卡 —— 本轮产生了交付产物且后端回传了任务目录时，
            // 追入 task_cleanup_confirm 卡片询问是否清理中间文件（纯问答轮不打扰）
            if (evt.taskId && evt.taskDir && hasTaskFiles) {
              st.appendToTab(tabId, {
                id: `task-cleanup-${Date.now()}`,
                role: 'system',
                kind: 'task_cleanup_confirm',
                content: JSON.stringify({ taskId: evt.taskId, taskDir: evt.taskDir }),
                status: 'running',
              });
            }
            // sessions 归档（2026-08-07，best-effort）：把归属页签最后一条 assistant 回复写进后端
            const tab = useChatStore.getState().tabs.find((t) => t.id === tabId);
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
          }
          void refreshThinking();
          break;
        }

        case 'error': {
          // 流异常终止 → 显示错误并按 run 解除归属；kind='error' 渲染「重试」按钮
          // BUGFIX #150：双发幂等同 done（runTabMap 归属作幂等键）
          const stErr = useChatStore.getState();
          const runId = (evt as { runId?: string }).runId ?? stErr.attributeRun(undefined);
          const tabId = tabFor(runId);
          const firstError = stErr.isRunKnown(runId);
          stErr.endRun(runId);
          if (!firstError) break;
          // 2026-08-19：异常终止也汇总已发生的改动（避免累积泄漏到下一轮）
          const changedErr = stErr.changedFilesByRun[runId] ?? [];
          if (changedErr.length > 0) {
            stErr.appendToTab(tabId, {
              id: `changed-files-${Date.now()}`,
              role: 'system',
              kind: 'changed_files',
              content: JSON.stringify(changedErr),
              status: 'ok',
            });
          }
          stErr.appendToTab(tabId, {
            id: `error-${Date.now()}`,
            role: 'system',
            kind: 'error',
            content: `运行出错：${(evt as { message?: string }).message ?? '未知错误'}`,
          });
          break;
        }
      }
    });

    // BUGFIX #161 看门狗巡检：心跳/业务事件均缺席超阈且仍 busy → 判定 SSE 静默
    // 断连（后端图任务已随流被取消，done 永远不会到），主动解锁并提示，
    // 防前端永久卡「思考中」（实测案例：HITL 审批后连接断、决策成孤儿）。
    const watchdog = setInterval(() => {
      if (useChatStore.getState().busyTabIds.length === 0) return;
      runWatchdogTick({
        busyTabCount: () => useChatStore.getState().busyTabIds.length,
        releaseAll: () => {
          const cur = useChatStore.getState();
          for (const [runId, tabId] of Object.entries(cur.runTabMap)) {
            cur.endRun(runId);
            cur.appendToTab(tabId, {
              id: `watchdog-${runId}`,
              role: 'system',
              kind: 'error',
              content: `与后端的连接已中断（超过 ${WATCHDOG_SILENCE_MS / 1000} 秒无任何事件），本轮任务可能已终止，请重试或重新发送。`,
            });
          }
        },
      });
    }, WATCHDOG_TICK_MS);

    return () => {
      unsubscribe();
      clearInterval(watchdog);
    };
  }, [appendTrace, update, setThinkingSession, refreshThinking]);
}
