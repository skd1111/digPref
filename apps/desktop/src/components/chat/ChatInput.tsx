/**
 * ChatInput —— textarea + 发送按钮。
 *
 * 提交时：
 *   1. 将用户消息写入 chatStore
 *   2. 调用 Rust `agent_chat` 命令，启动 SSE 流
 *   3. 事件通过 useAgentStream hook 自动路由到 store 和终端
 *
 * Phase 12 V1 新增：
 *   - 显示 Monaco 选区 chip（用户在编辑器右键「📋 附加选区到对话」时设置）
 *   - 发送时：选区作为独立 system 消息写入 chatStore，让 agent 知道用户关注点
 *
 * 借鉴 VSCode 内联聊天交互：
 *   - Enter 发送，Shift+Enter 换行（与 VSCode Copilot Chat 一致）
 *   - 发送中禁用输入，显示 loading 状态
 *   - 错误时显示内联提示
 */
import { useState, useCallback, useEffect, useMemo, type KeyboardEvent } from 'react';
import { invoke, ipc } from '@/ipc/invoke';
import { useChatStore, useFeatureContextPromptSnippet, useExpertTeamPromptSnippet } from '@/store/chatStore';
import { parseClarifyBlock } from '@/lib/clarify';
import { ClarifyCard } from '@/components/chat/ClarifyCard';
import { ExpertTeamSelector } from '@/components/chat/ExpertTeamSelector';
import { CHAT_RETRY_EVENT, CHAT_SEND_EVENT } from '@/components/chat/ChatMessage';
import { useUIStore } from '@/store/uiStore';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useBiznavStore } from '@/store/biznavStore';
import { useReqcardStore, buildDoneCardsSnippet } from '@/store/reqcardStore';
// 2026-08-05：推理模式 / 会话自主性开关已迁至 设置 → 高级设置
// （AdvancedSettingsPanel），不再占用发送按钮旁空间；值仍由 chatStore 透传。

/** 项目画像缓存（init 风格，2026-08-05）：同一工程一次会话只拉一次 */
const profileCache = new Map<string, string>();

async function getProjectProfile(projectName: string): Promise<string> {
  const cached = profileCache.get(projectName);
  if (cached !== undefined) return cached;
  try {
    const resp = await ipc.biznavProfile(projectName);
    const text = resp.has_profile ? resp.profile : '';
    profileCache.set(projectName, text);
    return text;
  } catch {
    return '';  // 画像拉取失败不阻塞发消息
  }
}

export function ChatInput(): JSX.Element {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const appendChat = useChatStore((s) => s.append);
  const setBusyStore = useChatStore((s) => s.setBusy);
  const storeBusy = useChatStore((s) => s.busy);  // 2026-08-07：agent 运行态（驱动停止按钮）
  const runId = useChatStore((s) => s.runId);
  const setRunId = useChatStore((s) => s.setRunId);
  const autonomy = useChatStore((s) => s.autonomy);  // Phase 18
  const inferenceMode = useChatStore((s) => s.inferenceMode);  // Phase 4 V0 推理模式透传
  const workMode = useUIStore((s) => s.mode);  // Phase 18：前端模式透传
  const chatSelection = useCodeNavStore((s) => s.chatSelection);
  const clearChatSelection = useCodeNavStore((s) => s.clearChatSelection);
  // 已选定业务功能点上下文 + 已导入工程名（发送时前置注入 prompt，
  // 让模型不再反问「哪个功能 / 什么语言 / 哪个项目」）
  const featureSnippet = useFeatureContextPromptSnippet();
  // 专家团上下文（运营工作台自动/手动选择后，发送时拼接）
  const expertTeamSnippet = useExpertTeamPromptSnippet();
  const projectName = useBiznavStore((s) => s.projectName);
  // reqflow V1：需求对齐中 → 发送时额外注入本工程已完成需求参照
  const alignmentActive = useReqcardStore((s) => s.alignment.active);

  // 选项式追问（2026-08-05）：最后一条 assistant 消息含 clarify 块时，
  // 在输入框上方渲染选项卡片；用户回复后新消息追加 → 卡片自然消失
  const tabs = useChatStore((s) => s.tabs);
  const activeTabId = useChatStore((s) => s.activeTabId);
  const clarify = useMemo(() => {
    const tab = tabs.find((t) => t.id === activeTabId);
    if (!tab || tab.messages.length === 0) return null;
    const last = tab.messages[tab.messages.length - 1];
    if (last.role !== 'assistant' || !last.content) return null;
    return parseClarifyBlock(last.content);
  }, [tabs, activeTabId]);
  const clarifyMsgId = useMemo(() => {
    const tab = tabs.find((t) => t.id === activeTabId);
    return tab && tab.messages.length > 0 ? tab.messages[tab.messages.length - 1].id : '';
  }, [tabs, activeTabId]);

  // 上下文可视化 chips（2026-08-07）：让用户看到「这次发送会带上什么」
  const ctxChips = useMemo(() => {
    const chips: string[] = [];
    if (featureSnippet) chips.push('📌 功能点上下文');
    if (expertTeamSnippet) chips.push('👥 专家团上下文');
    if (projectName) chips.push('🏷️ 项目画像');
    if (alignmentActive) chips.push('🔗 需求对齐参照');
    const tab = tabs.find((t) => t.id === activeTabId);
    if (tab) {
      const turns = Math.floor(
        tab.messages.filter((m) => (m.role === 'user' || m.role === 'assistant') && m.content)
          .length / 2,
      );
      if (turns > 0) chips.push(`💬 会话历史（近 ${Math.min(turns, 12)} 轮）`);
    }
    return chips;
  }, [featureSnippet, expertTeamSnippet, projectName, alignmentActive, tabs, activeTabId]);

  /**
   * sessions 归档（2026-08-07）：tab 首次发送时 sessionsCreate，后续追加。
   * 全链 fire-and-forget：Agent 未就绪/网络失败都静默，不阻塞对话。
   */
  const archiveUserMessage = useCallback((text: string): void => {
    const s = useChatStore.getState();
    const tab = s.tabs.find((t) => t.id === s.activeTabId);
    if (!tab) return;
    const sid = tab.backendSessionId;
    if (sid) {
      void ipc
        .sessionsAppendMessage(sid, { role: 'user', content: text })
        .catch((e) => console.warn('[sessions] 归档追加消息失败（不阻塞对话）:', e));
      return;
    }
    void ipc
      .sessionsCreate({
        title: (tab.title !== '新会话' ? tab.title : text).slice(0, 64),
        metadata: { source: 'chat_tab', tab_id: tab.id },
      })
      .then((created) => {
        useChatStore.getState().setTabSessionId(tab.id, created.id);
        return ipc.sessionsAppendMessage(created.id, { role: 'user', content: text });
      })
      .catch((e) => console.warn('[sessions] 会话归档创建失败（不阻塞对话）:', e));
  }, []);

  /** 共享发送管道：普通输入与 ClarifyCard 回复都走这里 */
  const sendUserMessage = useCallback(async (rawText: string): Promise<void> => {
    const trimmed = rawText.trim();
    // 本地 busy（发送中）与 store busy（agent 运行中）都拦，避免并发 run
    if (!trimmed || busy || useChatStore.getState().busy) return;

    // Phase 12 V1：如果附着了选区，先把它作为一条 system 消息写入 chatStore，
    // 让 agent 在 SSE 流中知道「用户关注以下代码」。
    if (chatSelection) {
      appendChat({
        id: `sel-${Date.now()}`,
        role: 'system',
        kind: 'execution',
        category: 'log',
        content: `[用户关注以下代码 · ${chatSelection.label} · 来自 ${shortFile(chatSelection.file)}]\n\`\`\`\n${chatSelection.text}\n\`\`\``,
        status: 'ok',
      });
    }

    // 添加用户消息到聊天（UI 只展示用户原文，上下文只进后端 prompt）
    const userMsgId = `user-${Date.now()}`;
    appendChat({
      id: userMsgId,
      role: 'user',
      content: trimmed,
    });

    setBusy(true);
    setBusyStore(true);
    setError(null);
    // 2026-08-07：整轮耗时起点 + 清上一轮结果
    useChatStore.getState().setRunStartTs(Date.now());
    useChatStore.getState().setLastRunMs(null);
    try {
      // 项目画像（init 风格）+ 选定功能点上下文 + 已完成需求参照 → 前置注入 prompt
      const profile = projectName ? await getProjectProfile(projectName) : '';
      const doneSnippet = alignmentActive ? buildDoneCardsSnippet() : '';
      const contextParts = [profile, featureSnippet, expertTeamSnippet, doneSnippet].filter((p) => p.length > 0);
      const finalPrompt =
        contextParts.length > 0
          ? `${contextParts.join('\n\n')}\n\n【用户问题】\n${trimmed}`
          : trimmed;

      // 发送时把「附加选区」也告诉 agent —— 后端 system prompt 会改写
      // Phase 18：workMode/autonomy 随请求透传（ModeRouter 先验 + HITL 决策矩阵）
      // inferenceMode：性能模式下后端注入完整版双模式系统提示词
      // 2026-08-07：agent_chat 返回 run_id，存入 store 供「停止」按钮取消用
      // history：存量断线修复 —— Rust/后端早已支持，前端一直没传，导致跨轮上下文丢失；
      // 取当前 tab 最近 24 条 user/assistant（排除刚追加的本条，后端会再清洗）
      const histSrc =
        useChatStore
          .getState()
          .tabs.find((t) => t.id === useChatStore.getState().activeTabId)
          ?.messages ?? [];
      const history = histSrc
        .filter(
          (m) =>
            (m.role === 'user' || m.role === 'assistant') &&
            m.content &&
            m.id !== userMsgId,
        )
        .slice(-24)
        .map((m) => ({ role: m.role, content: m.content }));

      const newRunId = await invoke<string>('agent_chat', {
        prompt: finalPrompt,
        workMode,
        autonomy,
        inferenceMode,
        history,
        selection: chatSelection
          ? {
              file: chatSelection.file,
              start_line: chatSelection.startLine,
              end_line: chatSelection.endLine,
              text: chatSelection.text,
            }
          : null,
      });
      setRunId(newRunId);

      // sessions 后端归档（2026-08-07，best-effort）：首次发送懒创建 session，
      // 之后逐条追加；全链 fire-and-forget，失败不影响主对话
      archiveUserMessage(trimmed);
    } catch (e) {
      setError(String(e));
      setBusyStore(false);
    } finally {
      setText('');
      setBusy(false);
      // 发送后清掉选区（一次性附加）
      clearChatSelection();
    }
  }, [busy, chatSelection, appendChat, setBusyStore, setRunId, clearChatSelection, workMode, autonomy, inferenceMode, featureSnippet, expertTeamSnippet, projectName, alignmentActive, archiveUserMessage]);

  const submit = useCallback(async (): Promise<void> => {
    await sendUserMessage(text);
  }, [sendUserMessage, text]);

  /** 停止当前 run：调 Rust agent_cancel（后端 SSE 桥会补发 done(cancelled) 解除 busy） */
  const stopRun = useCallback((): void => {
    if (!runId) return;
    void ipc.cancel(runId).catch(() => undefined);
    // 前端乐观解锁，不等后端事件；useAgentStream 的 done 事件会再刷一次双保险
    setBusyStore(false);
  }, [runId, setBusyStore]);

  // 错误消息「重试」：ChatMessage 发 CHAT_RETRY_EVENT → 重发最后一条用户消息
  useEffect(() => {
    const onRetry = (): void => {
      const s = useChatStore.getState();
      if (s.busy) return;
      const tab = s.tabs.find((t) => t.id === s.activeTabId);
      if (!tab) return;
      for (let i = tab.messages.length - 1; i >= 0; i--) {
        const m = tab.messages[i];
        if (m.role === 'user' && m.content) {
          void sendUserMessage(m.content);
          return;
        }
      }
    };
    window.addEventListener(CHAT_RETRY_EVENT, onRetry);
    return () => window.removeEventListener(CHAT_RETRY_EVENT, onRetry);
  }, [sendUserMessage]);

  // 欢迎页快捷提问（2026-08-07）：示例卡点击 → 携带文本直接走发送管道
  useEffect(() => {
    const onQuickSend = (e: Event): void => {
      const text = (e as CustomEvent<string>).detail;
      if (typeof text === 'string' && text.trim()) {
        void sendUserMessage(text);
      }
    };
    window.addEventListener(CHAT_SEND_EVENT, onQuickSend);
    return () => window.removeEventListener(CHAT_SEND_EVENT, onQuickSend);
  }, [sendUserMessage]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter 发送，Shift+Enter 换行（与 VSCode Copilot Chat 一致）
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        void submit();
      }
    },
    [submit],
  );

  return (
    <div className="flex flex-col gap-1">
      {error && (
        <div className="rounded border border-accent-danger bg-bg-code px-3 py-1 text-xs text-accent-danger">
          {error}
        </div>
      )}

      {/* Phase 12 V1：附加选区 chip —— 让用户看到「我附加了哪段代码」
          自动同步（auto=true，编辑器选区变化触发）：灰色边框，提示「会自动更新」
          手动附加（auto=false，右键菜单触发）：绿色边框，提示「手动选择保留」 */}
      {chatSelection && (
        <div
          className="flex items-center gap-2 rounded px-2 py-1 text-2xs"
          style={{
            backgroundColor: '#f5f5f4',
            border: `1px solid ${chatSelection.auto ? '#9ca3af' : '#10a37f'}`,
            color: '#202124',
          }}
        >
          <span style={{ color: chatSelection.auto ? '#6b7280' : '#10a37f' }}>
            {chatSelection.auto ? '📋' : '📌'}
          </span>
          <span className="font-mono" style={{ color: chatSelection.auto ? '#2563eb' : '#10a37f' }}>
            {chatSelection.auto ? '已自动附加选中' : '已手动附加选中'}
          </span>
          <span className="font-mono" style={{ color: '#0891b2' }}>
            {chatSelection.label}
          </span>
          <span className="truncate" style={{ color: '#6b7280' }}>
            · {shortFile(chatSelection.file)}
          </span>
          <button
            type="button"
            onClick={() => clearChatSelection()}
            title="移除选区"
            className="ml-auto rounded px-1.5 hover:bg-[#e7e5e4]"
            style={{ color: '#6b7280' }}
          >
            ✕
          </button>
        </div>
      )}

      {/* 上下文可视化 chips（2026-08-07）：发送时会附加的上下文一目了然 */}
      {ctxChips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[10px]" style={{ color: '#9ca3af' }}>
            将附加上下文：
          </span>
          {ctxChips.map((c) => (
            <span
              key={c}
              className="rounded-full border px-2 py-0.5 text-[10px]"
              style={{ backgroundColor: '#fafaf9', borderColor: '#e7e5e4', color: '#6b7280' }}
            >
              {c}
            </span>
          ))}
        </div>
      )}

      <div
        className={clarify ? 'rounded-b border bg-bg-subtle p-2' : undefined}
        style={clarify ? { borderColor: '#e7e5e4', borderTop: '1px solid #e7e5e4', backgroundColor: '#fafaf9' } : undefined}
      >
        {/* 选项式追问卡片：与输入框连成一体（key=消息 id，新一轮追问重置作答状态） */}
        {clarify && (
          <div className="-m-2 mb-2">
            <ClarifyCard
              key={clarifyMsgId}
              questions={clarify.questions}
              busy={busy}
              onSend={(t) => void sendUserMessage(t)}
            />
          </div>
        )}
        {/* aicss AI Agent Input 风格（2026-08-10）：圆角整体框 + 底部工具栏 + 圆形发送键 */}
        <div
          className="chat-input-frame rounded-xl border bg-white shadow-sm transition-shadow focus-within:shadow-md"
          style={{ borderColor: '#e7e5e4' }}
        >
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={busy}
            placeholder={
              storeBusy
                ? 'Agent 正在处理…（可先输入下一个问题，或点停止）'
                : chatSelection
                  ? `告诉我你想做什么（已附加 ${chatSelection.label}）…`
                  : '告诉我你想做什么… (Enter 发送, Shift+Enter 换行)'
            }
            className="block w-full resize-none bg-transparent px-3 pt-2.5 pb-1 text-sm focus:outline-none disabled:opacity-50"
            rows={2}
          />
          <div className="flex items-center gap-2 px-2 pb-2">
            {/* 专家团选择器（仅运营工作台显示，其余页签零占位） */}
            <ExpertTeamSelector />
            <span className="ml-auto hidden text-[10px] sm:inline" style={{ color: '#9ca3af' }}>
              Enter 发送 · Shift+Enter 换行
            </span>
            {storeBusy ? (
              // 运行中 → 停止按钮（红调圆形，Codex 风格）
              <button
                onClick={stopRun}
                disabled={!runId}
                title="停止当前任务"
                className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full border transition-colors hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-40"
                style={{ borderColor: '#dc2626', color: '#dc2626', backgroundColor: '#ffffff' }}
              >
                <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" aria-hidden="true">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
              </button>
            ) : (
              <button
                onClick={() => void submit()}
                disabled={busy || !text.trim()}
                title="发送"
                className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full transition-all disabled:cursor-not-allowed"
                style={{
                  backgroundColor: busy || !text.trim() ? '#e7e5e4' : '#10a37f',
                  color: busy || !text.trim() ? '#9ca3af' : '#ffffff',
                }}
              >
                {busy ? (
                  <span
                    className="animate-spin-ring rounded-full"
                    style={{ width: 12, height: 12, border: '2px solid #ffffff66', borderTopColor: '#ffffff' }}
                  />
                ) : (
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 19V5m0 0-6 6m6-6 6 6" />
                  </svg>
                )}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 把绝对路径截短为「相对项目根」或文件名，方便 chip 显示 */
function shortFile(path: string): string {
  if (!path) return '?';
  // Windows: 取最后两段（D:/code/myproject/src/foo.ts → myproject/src/foo.ts）
  const parts = path.split(/[/\\]/).filter(Boolean);
  if (parts.length <= 2) return path;
  return '…/' + parts.slice(-2).join('/');
}