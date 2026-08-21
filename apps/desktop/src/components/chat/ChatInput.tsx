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
 *   - 发送时：选区代码拼进 prompt 上下文段（2026-08-19 修复，此前代码未到达后端），
 *     不再单独写 system 日志消息（用户不需要界面提示）
 *
 * 附加文件（2026-08-14）：
 *   - 工具栏 📎 按钮选本地文件（代码/文本直读；docx/pdf 等后端转 Markdown）
 *   - 发送时把附件内容拼进 prompt 上下文段（lib/attachments.ts）
 *
 * 借鉴 VSCode 内联聊天交互：
 *   - Enter 发送，Shift+Enter 换行（与 VSCode Copilot Chat 一致）
 *   - 发送中禁用输入，显示 loading 状态
 *   - 错误时显示内联提示
 */
import { useState, useCallback, useEffect, useMemo, useRef, type KeyboardEvent } from 'react';
import { invoke, ipc } from '@/ipc/invoke';
import { useChatStore, useFeatureContextPromptSnippet, useExpertTeamPromptSnippet, tabContextMessages, estimateHistoryTokens } from '@/store/chatStore';
import { parseClarifyBlock } from '@/lib/clarify';
import {
  buildAttachmentsSnippet,
  readFileAsBase64,
  MAX_ATTACHMENTS,
  type ChatAttachment,
} from '@/lib/attachments';
import { ClarifyCard } from '@/components/chat/ClarifyCard';
import { ExpertTeamSelector } from '@/components/chat/ExpertTeamSelector';
import { CHAT_RETRY_EVENT, CHAT_SEND_EVENT } from '@/components/chat/ChatMessage';
import { useUIStore } from '@/store/uiStore';
import { useCodeNavStore } from '@/store/codeNavStore';
import { useBiznavStore } from '@/store/biznavStore';
import { useExpertTeamStore } from '@/store/expertTeamStore';
import { useReqcardStore, buildDoneCardsSnippet } from '@/store/reqcardStore';
// 2026-08-05：推理模式 / 会话自主性开关已迁至 设置 → 高级设置
// （AdvancedSettingsPanel），不再占用发送按钮旁空间；值仍由 chatStore 透传。

/** 项目画像缓存（init 风格，2026-08-05）：同一工程一次会话只拉一次 */
const profileCache = new Map<string, string>();

/** 压缩时保留最近 5 轮原文（10 条 user/assistant），其余进 LLM 摘要（2026-08-17） */
const COMPRESS_KEEP_MESSAGES = 10;
/** 上下文大小警示阈值（估算 token）：超过后指示器变橙提醒可压缩/清理 */
const CTX_WARN_TOKENS = 8000;

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
  const [dragOver, setDragOver] = useState(false);
  // 项目画像停用（2026-08-14）：值为被停用的工程名 —— 该工程发送时不再注入画像，
  // 切到其他工程自动恢复（用户偶尔做无关小需求时不想被画像带偏）
  const [profileSuppressed, setProfileSuppressed] = useState<string | null>(null);
  // 附加文件（2026-08-14）：📎 选中 → 后端转文本 → 发送时拼进 prompt
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // 供 addFiles（空依赖 useCallback）读当前数量做上限检查
  const attachmentsRef = useRef<ChatAttachment[]>([]);
  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);
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

  // 纯净对话模式（2026-08-14，按页签）：发送时不注入任何项目上下文，
  // 用于与工程无关的小问题；用户主动附加的附件/选区不受影响
  const cleanMode = useMemo(
    () => tabs.find((t) => t.id === activeTabId)?.cleanMode === true,
    [tabs, activeTabId],
  );
  const toggleCleanMode = useCallback((): void => {
    useChatStore.getState().setTabCleanMode(activeTabId, !cleanMode);
  }, [activeTabId, cleanMode]);

  // 会话模型选择（2026-08-17）：选项 = 模型管理已启用模型；选中随发送透传
  // 后端（优先级最高），未选回落模型管理路由配置；按页签记忆（随 tabs 持久化）
  const chatModel = useMemo(
    () => tabs.find((t) => t.id === activeTabId)?.chatModel,
    [tabs, activeTabId],
  );

  // 上下文管理（2026-08-17）：大小指示器 + 断点式清理 + LLM 压缩菜单
  const [ctxMenuOpen, setCtxMenuOpen] = useState(false);
  const [compressing, setCompressing] = useState(false);
  const activeTab = useMemo(() => tabs.find((t) => t.id === activeTabId), [tabs, activeTabId]);
  // 将随下次发送的会话 history（断点过滤后）与估算 token
  const historyMsgs = useMemo(() => (activeTab ? tabContextMessages(activeTab) : []), [activeTab]);
  const historyTokens = useMemo(() => (activeTab ? estimateHistoryTokens(activeTab) : 0), [activeTab]);
  const [modelOptions, setModelOptions] = useState<Array<{ name: string; label: string }>>([]);
  const refreshModelOptions = useCallback(async (): Promise<void> => {
    try {
      const r = await ipc.routerListBackends();
      setModelOptions(
        r.backends
          .filter((b) => b.enabled)
          .map((b) => ({ name: b.name, label: `${b.name} · ${b.model_name}` })),
      );
    } catch {
      // Agent 未就绪：选项留空不阻塞发送（选中项仍在，就绪后自然可选）
    }
  }, []);
  useEffect(() => {
    void refreshModelOptions();
  }, [refreshModelOptions]);

  // 上下文可视化 chips（2026-08-07）：让用户看到「这次发送会带上什么」
  // 2026-08-14：用户主动选择的上下文（功能点/专家团/需求对齐）支持 ✕ 移除，
  // 清除底层选中态；项目画像支持按工程停用（虚线 chip 点一下可恢复）；
  // 会话历史为自动类不可删
  const profileActive = Boolean(projectName) && profileSuppressed !== projectName;
  const ctxChips = useMemo(() => {
    const chips: Array<{ label: string; onRemove?: () => void }> = [];
    // 纯净对话模式：项目类上下文全部不注入（后端系统提示词不受影响），
    // 只展示开关本身 + 用户主动附加项
    if (cleanMode) {
      chips.push({ label: '🌙 纯净对话（排除项目上下文，系统提示词照常）' });
    }
    if (!cleanMode && featureSnippet) {
      chips.push({
        label: '📌 功能点上下文',
        onRemove: () => {
          useChatStore.getState().setFeatureContext(null);
          useChatStore.getState().setOpsNavContext(null);
        },
      });
    }
    if (!cleanMode && expertTeamSnippet) {
      chips.push({
        label: '👥 专家团上下文',
        onRemove: () => useExpertTeamStore.getState().clearSelection(),
      });
    }
    if (!cleanMode && projectName && profileSuppressed !== projectName) {
      chips.push({
        label: '🏷️ 项目画像',
        onRemove: () => setProfileSuppressed(projectName),
      });
    }
    if (!cleanMode && alignmentActive) {
      chips.push({
        label: '🔗 需求对齐参照',
        // 与 ReqAlignmentBanner「取消对齐」同套清理
        onRemove: () => {
          useReqcardStore.getState().cancelAlignment();
          useChatStore.getState().setAlignmentFeatures(null);
        },
      });
    }
    const readyFiles = attachments.filter((a) => a.status === 'ready').length;
    if (readyFiles > 0) chips.push({ label: `📎 附加文件 ×${readyFiles}` });
    if (activeTab?.contextSummary) {
      chips.push({ label: '📦 历史压缩摘要' });
    }
    const turns = Math.floor(historyMsgs.length / 2);
    if (turns > 0) {
      chips.push({ label: `💬 会话历史（近 ${Math.min(turns, 12)} 轮 · ≈${formatChars(historyTokens)} tok）` });
    }
    return chips;
  }, [featureSnippet, expertTeamSnippet, projectName, alignmentActive, attachments, activeTab, historyMsgs, historyTokens, profileSuppressed, cleanMode]);

  /** 📎 选中文件：逐个读 base64 → 后端转文本，状态写回 attachments */
  const addFiles = useCallback(async (list: FileList | File[]): Promise<void> => {
    const files = [...list];
    if (files.length === 0) return;
    const room = MAX_ATTACHMENTS - attachmentsRef.current.length;
    const accepted = files.slice(0, Math.max(0, room));
    if (accepted.length < files.length) {
      setError(`最多附加 ${MAX_ATTACHMENTS} 个文件，多余的已忽略`);
    }
    for (const file of accepted) {
      const id = `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setAttachments((prev) => [
        ...prev,
        { id, name: file.name, status: 'uploading', content: '', chars: 0, truncated: false, mode: 'text', error: '' },
      ]);
      try {
        const b64 = await readFileAsBase64(file);
        const res = await ipc.chatAttachFile({ file_name: file.name, content_base64: b64 });
        setAttachments((prev) =>
          prev.map((a) =>
            a.id === id
              ? {
                  ...a,
                  status: res.ok ? 'ready' : 'error',
                  content: res.content,
                  chars: res.chars,
                  truncated: res.truncated,
                  mode: res.mode,
                  error: res.error,
                }
              : a,
          ),
        );
      } catch (e) {
        setAttachments((prev) =>
          prev.map((a) => (a.id === id ? { ...a, status: 'error', error: String(e) } : a)),
        );
      }
    }
  }, []);

  const removeAttachment = useCallback((id: string): void => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

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

    // 附加文件（2026-08-14）：就绪的附件写一条 system 提示，内容只进后端 prompt
    const readyAttachments = attachments.filter((a) => a.status === 'ready' && a.content);
    if (readyAttachments.length > 0) {
      appendChat({
        id: `att-${Date.now()}`,
        role: 'system',
        kind: 'execution',
        category: 'log',
        content: `[用户附加了 ${readyAttachments.length} 个文件：${readyAttachments
          .map((a) => a.name)
          .join('、')}]`,
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
      // 项目画像（init 风格）+ 选定功能点上下文 + 已完成需求参照 + 附加文件 → 前置注入 prompt
      // 纯净对话模式（cleanMode）下项目类上下文全部跳过，只保留用户主动附加项
      const profile = !cleanMode && profileActive && projectName ? await getProjectProfile(projectName) : '';
      const doneSnippet = !cleanMode && alignmentActive ? buildDoneCardsSnippet() : '';
      const attachSnippet = buildAttachmentsSnippet(attachments) ?? '';
      // 选区代码直接拼进 prompt（2026-08-19 修复）：之前只写了条 system 消息进 chatStore，
      // 但 history 只带 user/assistant、agent_chat 也没有 selection 参数 → 代码从未到达后端，
      // 导致模型反问「想了解哪个类」。cleanMode 下仍保留（用户主动关注项，同附件）
      const selSnippet = chatSelection
        ? `[用户当前关注的代码 · ${chatSelection.label} · 来自 ${shortFile(chatSelection.file)}]\n\`\`\`\n${chatSelection.text}\n\`\`\``
        : '';
      const contextParts = [profile, cleanMode ? '' : featureSnippet, cleanMode ? '' : expertTeamSnippet, doneSnippet, selSnippet, attachSnippet].filter((p) => p.length > 0);
      const finalPrompt =
        contextParts.length > 0
          ? `${contextParts.join('\n\n')}\n\n【用户问题】\n${trimmed}`
          : trimmed;

      // Phase 18：workMode/autonomy 随请求透传（ModeRouter 先验 + HITL 决策矩阵）
      // inferenceMode：性能模式下后端注入完整版双模式系统提示词
      // 2026-08-07：agent_chat 返回 run_id，存入 store 供「停止」按钮取消用
      // history：存量断线修复 —— Rust/后端早已支持，前端一直没传，导致跨轮上下文丢失；
      // 取断点之后最近 24 条 user/assistant（排除刚追加的本条，后端会再清洗）；
      // 断点之前的旧对话已压缩成摘要（historySummary）随请求透传
      const chatState = useChatStore.getState();
      const activeTab = chatState.tabs.find((t) => t.id === chatState.activeTabId);
      const history = (activeTab ? tabContextMessages(activeTab) : [])
        .filter((m) => m.id !== userMsgId)
        .slice(-24)
        .map((m) => ({ role: m.role, content: m.content }));

      const newRunId = await invoke<string>('agent_chat', {
        prompt: finalPrompt,
        workMode,
        autonomy,
        inferenceMode,
        history,
        // 历史压缩摘要（2026-08-17）：断点之前旧对话的 LLM 摘要，后端注入
        // graph 初始 messages 的 system 消息；未压缩过传 null
        historySummary: activeTab?.contextSummary ?? null,
        // 会话模型（2026-08-17）：本 tab 选中的模型管理 backend 名，后端置顶回答链；
        // 未选传 null → 后端清除 override，回落模型管理路由配置
        modelOverride: activeTab?.chatModel ?? null,
        // 页面上下文（2026-08-14）：当前会话页签名 + 模式随请求进后端，
        // 注入 intent/decompose prompt，消除「连接」这类模糊动词的场景歧义
        // （如当前页签就是「内网模型接入配置」时，「连接」= 写入接入配置）
        pageContext: { page: { workMode, tabTitle: activeTab?.title ?? '' } },
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
      // 发送后清掉选区与附加文件（一次性附加）
      clearChatSelection();
      setAttachments([]);
    }
  }, [busy, chatSelection, appendChat, setBusyStore, setRunId, clearChatSelection, workMode, autonomy, inferenceMode, featureSnippet, expertTeamSnippet, projectName, alignmentActive, archiveUserMessage, attachments, profileActive, cleanMode]);

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

  /** 清理上下文（2026-08-17，断点式）：界面消息保留可回看，但此后的发送
   *  不再携带之前的历史；同时作废旧的压缩摘要 */
  const clearContext = useCallback((): void => {
    const s = useChatStore.getState();
    if (s.busy || compressing) return;
    const tab = s.tabs.find((t) => t.id === activeTabId);
    if (!tab || tab.messages.length === 0) return;
    appendChat({
      id: `ctx-clear-${Date.now()}`,
      role: 'system',
      kind: 'execution',
      category: 'log',
      content: '— 上下文已清理：此后的对话不再携带之前的历史（界面消息仍保留） —',
      status: 'ok',
    });
    s.clearTabContext(activeTabId);
    setCtxMenuOpen(false);
  }, [activeTabId, appendChat, compressing]);

  /** 压缩上下文（2026-08-17）：保留最近 5 轮原文，其余旧对话交后端
   *  本地优先 LLM 链生成摘要替换；失败内联提示不清数据 */
  const compressContext = useCallback(async (): Promise<void> => {
    const s = useChatStore.getState();
    if (s.busy || compressing) return;
    const tab = s.tabs.find((t) => t.id === activeTabId);
    if (!tab) return;
    const msgs = tabContextMessages(tab);
    if (msgs.length <= COMPRESS_KEEP_MESSAGES) {
      setError(`历史不足 ${COMPRESS_KEEP_MESSAGES / 2} 轮，无需压缩（压缩会保留最近 ${COMPRESS_KEEP_MESSAGES / 2} 轮原文）`);
      setCtxMenuOpen(false);
      return;
    }
    const toCompress = msgs.slice(0, -COMPRESS_KEEP_MESSAGES);
    setCompressing(true);
    setError(null);
    try {
      const res = await ipc.chatCompressHistory({
        messages: toCompress.map((m) => ({ role: m.role, content: m.content })),
        ...(tab.contextSummary ? { historySummary: tab.contextSummary } : {}),
      });
      // 断点 = 最后一条被压缩的消息（断点及之前全部排除）；
      // 保留的最近 5 轮仍在断点之后，继续随发送进后端
      useChatStore
        .getState()
        .applyTabCompression(activeTabId, res.summary, toCompress[toCompress.length - 1].id);
      appendChat({
        id: `ctx-compress-${Date.now()}`,
        role: 'system',
        kind: 'execution',
        category: 'log',
        content: `📦 上下文已压缩：${Math.floor(toCompress.length / 2)} 轮旧对话 → 摘要（≈${formatChars(res.beforeTokens)} tok → ≈${formatChars(res.afterTokens)} tok，最近 ${COMPRESS_KEEP_MESSAGES / 2} 轮保留原文）`,
        status: 'ok',
      });
      setCtxMenuOpen(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setCompressing(false);
    }
  }, [activeTabId, appendChat, compressing]);

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

      {/* 附加文件 chips（2026-08-14）：转换中 / 就绪 / 失败三态，可单个移除 */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {attachments.map((a) => (
            <span
              key={a.id}
              className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]"
              style={{
                backgroundColor: a.status === 'error' ? '#fef2f2' : '#fafaf9',
                borderColor: a.status === 'error' ? '#fca5a5' : a.status === 'ready' ? '#10a37f' : '#e7e5e4',
                color: a.status === 'error' ? '#dc2626' : '#6b7280',
              }}
              title={a.status === 'error' ? a.error : `${a.chars} 字符${a.truncated ? '（已截断）' : ''}`}
            >
              <span>{a.status === 'uploading' ? '⏳' : a.status === 'error' ? '⚠️' : a.mode === 'markdown' ? '📄' : '📎'}</span>
              <span className="max-w-[160px] truncate">{a.name}</span>
              {a.status === 'ready' && <span style={{ color: '#9ca3af' }}>{formatChars(a.chars)}</span>}
              {a.status === 'error' && <span className="max-w-[120px] truncate">{a.error || '转换失败'}</span>}
              <button
                type="button"
                onClick={() => removeAttachment(a.id)}
                title="移除附件"
                className="rounded px-0.5 hover:opacity-60"
              >
                ✕
              </button>
            </span>
          ))}
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
              key={c.label}
              className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]"
              style={{ backgroundColor: '#fafaf9', borderColor: '#e7e5e4', color: '#6b7280' }}
            >
              {c.label}
              {c.onRemove && (
                <button
                  type="button"
                  onClick={c.onRemove}
                  title="移除此上下文（重新选择后可再次附加）"
                  className="rounded px-0.5 hover:opacity-60"
                >
                  ✕
                </button>
              )}
            </span>
          ))}
          {/* 项目画像已停用：虚线 chip，点一下恢复注入（2026-08-14） */}
          {projectName && profileSuppressed === projectName && (
            <button
              type="button"
              onClick={() => setProfileSuppressed(null)}
              title="项目画像已停用，点击恢复附加"
              className="rounded-full border border-dashed px-2 py-0.5 text-[10px] hover:opacity-70"
              style={{ borderColor: '#d6d3d1', color: '#a8a29e', backgroundColor: 'transparent' }}
            >
              🏷️ 项目画像（已停用）
            </button>
          )}
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
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const dropped = [...(e.dataTransfer?.files ?? [])];
            if (dropped.length > 0) void addFiles(dropped);
          }}
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
            {/* 🤖 会话模型选择（2026-08-17，按页签）：选中时本 tab 回答优先用该模型，
                选项来自模型管理已启用模型；默认跟随模型管理路由配置 */}
            <select
              value={chatModel ?? ''}
              onChange={(e) => {
                useChatStore.getState().setTabChatModel(activeTabId, e.target.value || null);
                void refreshModelOptions();
              }}
              onFocus={() => void refreshModelOptions()}
              disabled={busy}
              title={
                chatModel
                  ? `本页签回答优先使用「${chatModel}」（优先级最高，点击可切换）`
                  : '会话模型：默认按「设置 → 模型管理」路由配置；选中后本页签优先用该模型'
              }
              className="h-7 max-w-[150px] flex-shrink-0 truncate rounded-full border bg-white px-1.5 text-[10px] outline-none disabled:opacity-50"
              style={{
                borderColor: chatModel ? '#10a37f' : '#e7e5e4',
                color: chatModel ? '#10a37f' : '#6b7280',
              }}
            >
              <option value="">🤖 默认（模型管理）</option>
              {modelOptions.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.label}
                </option>
              ))}
            </select>
            {/* 🧠 上下文大小指示器 + 清理/压缩菜单（2026-08-17）：
                展示将随下次发送的会话 history 估算 token；点击展开操作菜单 */}
            <div className="relative flex-shrink-0">
              <button
                type="button"
                onClick={() => setCtxMenuOpen((v) => !v)}
                disabled={busy}
                title={`将随下次发送的会话历史 ≈${historyTokens.toLocaleString()} tokens（点击查看清理/压缩）`}
                className="flex h-7 items-center gap-1 rounded-full border px-2 text-[10px] transition-colors hover:opacity-80 disabled:opacity-50"
                style={{
                  borderColor: historyTokens >= CTX_WARN_TOKENS ? '#f59e0b' : '#e7e5e4',
                  color: historyTokens >= CTX_WARN_TOKENS ? '#b45309' : '#6b7280',
                  backgroundColor: historyTokens >= CTX_WARN_TOKENS ? '#fffbeb' : '#ffffff',
                }}
              >
                🧠 ≈{formatChars(historyTokens)} tok
              </button>
              {ctxMenuOpen && (
                <>
                  {/* 透明遮罩：点击外部关闭菜单 */}
                  <div className="fixed inset-0 z-10" onClick={() => setCtxMenuOpen(false)} />
                  <div
                    className="absolute bottom-8 left-0 z-20 w-64 rounded-lg border bg-white p-2 shadow-lg"
                    style={{ borderColor: '#e7e5e4' }}
                  >
                    <div className="px-1 pb-1.5 text-[10px]" style={{ color: '#6b7280' }}>
                      会话历史：{Math.floor(historyMsgs.length / 2)} 轮 · ≈{historyTokens.toLocaleString()} tokens
                      {activeTab?.contextSummary ? ' · 已含压缩摘要' : ''}
                    </div>
                    <button
                      type="button"
                      onClick={clearContext}
                      disabled={storeBusy || compressing || historyMsgs.length === 0}
                      className="mb-1 block w-full rounded px-2 py-1.5 text-left text-[11px] transition-colors hover:bg-[#f5f5f4] disabled:cursor-not-allowed disabled:opacity-40"
                      style={{ color: '#1f1f1f' }}
                      title="界面消息保留可回看，但此后的发送不再携带之前的历史"
                    >
                      🧹 清理上下文（断点，不删消息）
                    </button>
                    <button
                      type="button"
                      onClick={() => void compressContext()}
                      disabled={storeBusy || compressing || historyMsgs.length <= COMPRESS_KEEP_MESSAGES}
                      className="block w-full rounded px-2 py-1.5 text-left text-[11px] transition-colors hover:bg-[#f5f5f4] disabled:cursor-not-allowed disabled:opacity-40"
                      style={{ color: '#1f1f1f' }}
                      title={`把旧对话压缩成摘要替换历史（保留最近 ${COMPRESS_KEEP_MESSAGES / 2} 轮原文）`}
                    >
                      {compressing ? '⏳ 正在压缩…' : `📦 压缩上下文（保留最近 ${COMPRESS_KEEP_MESSAGES / 2} 轮）`}
                    </button>
                  </div>
                </>
              )}
            </div>
            {/* 🌙 纯净对话开关（2026-08-14，按页签）：开启后发送不附加任何项目上下文 */}
            <button
              type="button"
              onClick={toggleCleanMode}
              title={
                cleanMode
                  ? '纯净对话已开启：不附加项目画像/功能点/专家团等上下文，助手系统提示词照常生效（点击关闭）'
                  : '开启纯净对话：本页签发送时排除项目上下文，助手基础能力（系统提示词）不受影响，适合与工程无关的小问题'
              }
              className="flex h-7 flex-shrink-0 items-center gap-1 rounded-full border px-2 text-[10px] transition-colors hover:opacity-80"
              style={{
                borderColor: cleanMode ? '#10a37f' : '#e7e5e4',
                color: cleanMode ? '#10a37f' : '#9ca3af',
                backgroundColor: cleanMode ? '#f0fdf4' : '#ffffff',
              }}
            >
              🌙 纯净
            </button>
            {/* 📎 附加文件（2026-08-14）：代码/文本直读，docx/pdf 等后端转 Markdown */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                void addFiles(e.target.files ?? []);
                if (fileInputRef.current) fileInputRef.current.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || attachments.length >= MAX_ATTACHMENTS}
              title={dragOver ? '松开即附加文件' : `附加文件作为上下文（最多 ${MAX_ATTACHMENTS} 个，支持代码/文本/docx/pdf 等）`}
              className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full border transition-colors hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-40"
              style={{ borderColor: dragOver ? '#10a37f' : '#e7e5e4', color: '#6b7280', backgroundColor: '#ffffff' }}
            >
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
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

/** 字符数短显示（附件 chip 用）：1.2k / 12k */
function formatChars(n: number): string {
  if (n < 1000) return `${n}`;
  return `${(n / 1000).toFixed(1)}k`;
}