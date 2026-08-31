/**
 * CenterChatFlow — VSCode 风格的中央对话区。
 *
 *  ┌────────────────────────────────────────────────────┐
 *  │ TabBar (新会话 1 / 新会话 2  …)  [＋]  [split toggle] │
 *  ├────────────────────────────────────────────────────┤
 *  │                                                     │
 *  │   message stream                                    │
 *  │   (user ↔ assistant + code blocks)                  │
 *  │                                                     │
 *  ├────────────────────────────────────────────────────┤
 *  │ ChatInput (textarea + 发送)                          │
 *  └────────────────────────────────────────────────────┘
 *
 * 支持拆分（Editor Split）：uiStore.editorSplit =
 *   null        — 单栏
 *   'vertical'  — 左右两栏（各显示一个 tab）
 *   'horizontal'— 上下两栏
 */
import { useEffect, useRef, useState } from "react";
import type { ChatMessage as ChatMessageT } from "@eaide/shared-protocol";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { TabBar } from "@/components/chat/TabBar";
import { ExecutionBlock } from "@/components/chat/ExecutionBlock";
import { ExecutionTree } from "@/components/chat/ExecutionTree";
import { groupExecutionSteps } from "@/lib/executionGrouping";
import { useChatStore, type ChatTab } from "@/store/chatStore";
import { useUIStore } from "@/store/uiStore";
import { useCodeNavStore } from "@/store/codeNavStore";
import { CHAT_SEND_EVENT } from "@/components/chat/ChatMessage";
import { ContextChip } from "@/components/biznav/ContextChip";
import { ReqAlignmentBanner } from "@/components/reqflow/ReqAlignmentBanner";
import { CodeEditorPane } from "@/components/editor/CodeEditorPane";
import { LivePreviewPanel } from "@/components/preview/LivePreviewPanel";
import { PreviewButton } from "@/components/preview/PreviewButton";
import { usePreviewStore } from "@/store/previewStore";
import { AiThinkingIndicator } from "@/components/chat/AiStatus";

export function CenterChatFlow(): JSX.Element {
  const allTabs = useChatStore((s) => s.tabs);
  // 模式隔离（2026-08-11）：副栏会话/拆分页签只看当前模式的页签组
  const mode = useUIStore((s) => s.mode);
  const targetMode = mode === "operator" ? "operator" : "full";
  const tabs = allTabs.filter((t) => (t.mode ?? "full") === targetMode);
  const activeId = useChatStore((s) => s.activeTabId);
  // 会话内搜索（2026-08-07）
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const secondaryId = useUIStore((s) => s.secondaryTabId);
  const setSecondaryId = useUIStore((s) => s.setSecondaryTabId);
  const editorSplit = useUIStore((s) => s.editorSplit);
  const setEditorSplit = useUIStore((s) => s.setEditorSplit);
  // 中央拆分比例（2026-08-28）：分隔条可拖拽调节对话区与编辑器/预览区占比
  const chatPaneRatio = useUIStore((s) => s.chatPaneRatio);
  const splitRef = useRef<HTMLDivElement>(null);
  // 会话管理 activity：只读浏览历史会话，底部输入区整体隐藏（用户要求 2026-08-07）
  const activityId = useUIStore((s) => s.activityId);
  const previewOpen = usePreviewStore((s) => s.previewOpen);
  const setPreviewOpen = usePreviewStore((s) => s.setPreviewOpen);
  const activeFilePath = useCodeNavStore((s) => s.activeFilePath);

  const activeTab = tabs.find((t) => t.id === activeId);
  // 搜索命中数（仅当前活动 tab 的 user/assistant 消息）
  const matchCount = (() => {
    const q = searchText.trim().toLowerCase();
    if (!q || !activeTab) return 0;
    return activeTab.messages.filter(
      (m) =>
        (m.role === "user" || m.role === "assistant") &&
        (m.content ?? "").toLowerCase().includes(q),
    ).length;
  })();
  // 有代码文件打开时，副栏渲染编辑器；否则渲染副栏会话 Tab
  const hasCodeFiles = useCodeNavStore((s) => s.openFiles.length > 0);

  // 有打开的代码文件但 editorSplit 未设置时，自动启用拆分（保底：确保 File→OpenFile 后编辑器可见）
  useEffect(() => {
    if (hasCodeFiles && !editorSplit) {
      setEditorSplit("vertical");
    }
  }, [hasCodeFiles, editorSplit, setEditorSplit]);

  // 拆开后，自动给副栏一个不同的 tab
  useEffect(() => {
    if (editorSplit && !secondaryId && tabs.length > 1) {
      const other = tabs.find((t) => t.id !== activeId);
      if (other) setSecondaryId(other.id);
    }
    if (!editorSplit && secondaryId) setSecondaryId(null);
  }, [editorSplit, tabs, activeId, secondaryId, setSecondaryId]);

  const secondaryTab = tabs.find((t) => t.id === secondaryId);

  return (
    <div className="flex h-full flex-col">
      <TabBar />

      {/* split 工具条 */}
      <div
        className="flex h-[28px] items-center justify-end gap-1 border-b px-2"
        style={{ backgroundColor: "#f5f5f4", borderColor: "#e7e5e4" }}
      >
        {/* 会话内搜索（2026-08-07） */}
        <div className="mr-auto flex items-center gap-1">
          <SplitButton
            active={searchOpen}
            onClick={() => {
              setSearchOpen(!searchOpen);
              if (searchOpen) setSearchText("");
            }}
            label="🔍"
            title="会话内搜索"
          />
          {searchOpen && (
            <>
              <input
                autoFocus
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                placeholder="搜索会话内容…"
                className="rounded border px-2 py-0.5 text-2xs outline-none focus:border-[#10a37f]"
                style={{
                  backgroundColor: "#ffffff",
                  borderColor: "#e7e5e4",
                  color: "#202124",
                  width: 180,
                }}
              />
              {searchText.trim() !== "" && (
                <span
                  className="text-2xs"
                  style={{ color: matchCount > 0 ? "#10a37f" : "#dc2626" }}
                >
                  {matchCount} 处命中
                </span>
              )}
            </>
          )}
        </div>
        {/* Phase 15 V0：▶️ 预览按钮（编辑器工具栏） */}
        <PreviewButton currentFile={activeFilePath} />
        <span
          className="mx-1 h-4 w-px"
          style={{ backgroundColor: "#e7e5e4" }}
          aria-hidden="true"
        />
        {/* Phase 15 V0：预览面板切换（嵌入兜底模式） */}
        <SplitButton
          active={previewOpen}
          onClick={() => {
            setPreviewOpen(!previewOpen);
            if (!previewOpen && !editorSplit) setEditorSplit("vertical");
          }}
          label="◧"
          title="预览面板（内嵌模式）"
        />
        <SplitButton
          active={editorSplit === "vertical"}
          onClick={() =>
            setEditorSplit(editorSplit === "vertical" ? null : "vertical")
          }
          label="⇆"
          title="左右拆分"
        />
        <SplitButton
          active={editorSplit === "horizontal"}
          onClick={() =>
            setEditorSplit(editorSplit === "horizontal" ? null : "horizontal")
          }
          label="⇅"
          title="上下拆分"
        />
        {editorSplit && (
          <SplitButton
            active={false}
            onClick={() => setEditorSplit(null)}
            label="✕"
            title="合并"
          />
        )}
      </div>

      <div
        ref={splitRef}
        className="flex flex-1 overflow-hidden"
        style={{
          flexDirection: editorSplit === "horizontal" ? "column" : "row",
        }}
      >
        {/* 拆分时对话区按 chatPaneRatio 占宽/高（分隔条可拖拽，2026-08-28） */}
        <Pane
          tab={activeTab ?? null}
          flex={editorSplit ? chatPaneRatio : 1}
          searchText={searchOpen ? searchText : ""}
        />
        {editorSplit && (
          <>
            <Splitter
              orientation={
                editorSplit === "vertical" ? "vertical" : "horizontal"
              }
              containerRef={splitRef}
            />
            {hasCodeFiles ? (
              <div
                className="min-h-0 min-w-0 overflow-hidden"
                style={{ flex: 1 - chatPaneRatio }}
              >
                {previewOpen ? <LivePreviewPanel /> : <CodeEditorPane />}
              </div>
            ) : (
              <Pane
                tab={secondaryTab ?? null}
                flex={1 - chatPaneRatio}
                onChangeTab={(id) => setSecondaryId(id)}
                tabs={tabs}
                searchText={searchOpen ? searchText : ""}
              />
            )}
          </>
        )}
      </div>

      {activityId !== "sessions" && (
        <div
          className="border-t px-4 py-3"
          style={{ borderColor: "#e7e5e4", backgroundColor: "#f5f5f4" }}
        >
          {/* reqflow V1：需求对齐中横幅（发起改造需求后才显示） */}
          {/* skill 命中提示已改作对话流内的执行步骤卡（2026-08-28），输入框上方不再显示徽标 */}
          <ReqAlignmentBanner />
          <ContextChip />
          <ChatInput />
        </div>
      )}
    </div>
  );
}

// ---- 子组件 --------------------------------------------------------------

/** 欢迎页快捷提问示例（2026-08-07）：点击直接走 ChatInput 发送管道 */
const QUICK_PROMPTS: Array<{ icon: string; text: string }> = [
  { icon: "📖", text: "介绍下当前项目的业务功能" },
  { icon: "🔍", text: "帮我审核一份合同文档的风险" },
  { icon: "🗄️", text: "帮我写一个数据查询 SQL" },
  { icon: "📋", text: "把我的想法整理成需求卡片" },
];

function Pane({
  tab,
  flex,
  tabs,
  onChangeTab,
  searchText,
}: {
  tab: ChatTab | null;
  flex: number;
  tabs?: ChatTab[];
  onChangeTab?: (id: string) => void;
  searchText?: string;
}): JSX.Element {
  const [paneHighlight, setPaneHighlight] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const [chatWidth, setChatWidth] = useState(0);
  // 2026-08-07：busy + 最后一条 assistant 消息 → 流式光标；
  // 2026-08-26 多会话并发：只看当前页签是否在跑（其他页签的运行不影响本视图）
  const busy = useChatStore((s) => s.busyTabIds.includes(s.activeTabId));
  // 2026-08-07：整轮耗时（done 后由 useAgentStream 写入）
  const lastRunMs = useChatStore((s) => s.lastRunMs);
  const q = (searchText ?? "").trim().toLowerCase();
  const isHit = (m: ChatMessageT): boolean =>
    q !== "" &&
    (m.role === "user" || m.role === "assistant") &&
    (m.content ?? "").toLowerCase().includes(q);
  // 滚动跟随（2026-08-07）：只有用户贴底时才自动滚，往上翻历史不被打断
  const stickToBottom = useRef(true);
  const [showJumpBottom, setShowJumpBottom] = useState(false);

  /** 距底小于该阈值视为「贴底」，继续跟随新消息 */
  const STICK_THRESHOLD = 80;

  const handleScroll = (): void => {
    const el = listRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD;
    stickToBottom.current = near;
    setShowJumpBottom(!near);
  };

  const jumpToBottom = (): void => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    stickToBottom.current = true;
    setShowJumpBottom(false);
  };

  // 消息区实际宽度：供消息框「各占 chat 横向 2/5」限宽（px）
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const update = () => setChatWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 流式输出：消息内容持续更新 → 贴底时才自动滚到底（2026-08-07 跟随策略）
  const messages = tab?.messages ?? [];
  useEffect(() => {
    const el = listRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // 最后一条 assistant 消息 id：只有它在 busy 时显示打字机光标
  const lastAssistantId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return messages[i].id;
    }
    return null;
  })();

  // 任务进度待办卡迁居左侧「任务计划」面板（2026-08-28，SideBar 头部可切）；
  // 对话区不再渲染悬浮横幅，todo 消息仍留在 store 供左侧面板读取与历史归档。

  // 搜索定位（2026-08-07）：首个命中消息滚进视口中央
  useEffect(() => {
    if (!q) return;
    const first = messages.find(isHit);
    if (first) {
      document
        .getElementById(`msg-${first.id}`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  return (
    <div
      className="relative flex flex-col overflow-hidden"
      style={{
        flex,
        backgroundColor: "#ffffff",
        outline: paneHighlight ? "1px solid #10a37f" : "none",
      }}
      onMouseEnter={() => setPaneHighlight(true)}
      onMouseLeave={() => setPaneHighlight(false)}
    >
      {/* 副栏 tab 选择器 */}
      {tabs && onChangeTab && (
        <div
          className="flex h-[26px] items-center gap-1 border-b px-2"
          style={{ backgroundColor: "#f5f5f4", borderColor: "#e7e5e4" }}
        >
          <span className="text-2xs" style={{ color: "#6b7280" }}>副栏:</span>
          <select
            value={tab?.id ?? ""}
            onChange={(e) => onChangeTab(e.target.value)}
            className="rounded px-1 py-0.5 text-2xs outline-none"
            style={{
              backgroundColor: "#ffffff",
              color: "#202124",
              border: "1px solid #e7e5e4",
            }}
          >
            {tabs.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* 任务进度卡已迁居左侧「任务计划」面板（2026-08-28），对话区不再渲染横幅 */}

      <div
        ref={listRef}
        className="flex-1 overflow-auto px-6 py-4"
        onScroll={handleScroll}
      >
        {tab?.messages.length === 0 || !tab ? (
          <div className="flex h-full flex-col items-center justify-center text-fg-muted">
            <div className="mb-1 text-ui-lg font-semibold" style={{ color: "#202124" }}>
              EAIDE
            </div>
            <div className="mb-4 text-2xs" style={{ color: "#9ca3af" }}>
              企业内网本地化 AI 工作台 · 输入问题或点击下方示例开始
            </div>
            {/* 快捷提问示例卡（aicss 风格，2026-08-10：图标软底 + 圆角卡） */}
            <div className="grid grid-cols-2 gap-2">
              {QUICK_PROMPTS.map((p) => (
                <button
                  key={p.text}
                  type="button"
                  onClick={() =>
                    window.dispatchEvent(
                      new CustomEvent(CHAT_SEND_EVENT, { detail: p.text }),
                    )
                  }
                  className="flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left text-ui transition-all hover:-translate-y-px hover:border-[#10a37f] hover:shadow-sm"
                  style={{
                    backgroundColor: "#ffffff",
                    borderColor: "#e7e5e4",
                    color: "#374151",
                    minWidth: 210,
                  }}
                >
                  <span
                    className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg text-ui"
                    style={{ backgroundColor: "#10a37f14" }}
                    aria-hidden="true"
                  >
                    {p.icon}
                  </span>
                  <span className="truncate">{p.text}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          groupExecutionSteps(tab.messages).map((item) =>
            item.type === "tree" ? (
              <div key={`tree-${item.key}`} id={`msg-${item.key}`}>
                <ExecutionTree items={item.items} />
              </div>
            ) : (
              <div
                key={item.m.id}
                id={`msg-${item.m.id}`}
                className={isHit(item.m) ? "search-hit rounded" : undefined}
              >
                {item.m.role === "system" && item.m.kind === "execution" ? (
                  <ExecutionBlock
                    message={item.m}
                    {...(item.occurrence != null ? { occurrence: item.occurrence } : {})}
                  />
                ) : (
                  <ChatMessage
                    message={item.m}
                    maxWidth={chatWidth > 0 ? Math.round(chatWidth * 0.4) : undefined}
                    streaming={busy && item.m.id === lastAssistantId}
                  />
                )}
              </div>
            )
          )
        )}
        <ThinkingIndicator messages={tab?.messages ?? []} tabId={tab?.id ?? ""} />
        {/* 整轮耗时（2026-08-07）：上一轮 done 后展示 */}
        {!busy && lastRunMs != null && messages.length > 0 && (
          <div className="mt-1 text-center text-[10px]" style={{ color: "#9ca3af" }}>
            ✓ 本轮耗时 {(lastRunMs / 1000).toFixed(1)}s
          </div>
        )}
      </div>

      {/* 回到底部浮动按钮（往上翻历史时出现） */}
      {showJumpBottom && (
        <button
          type="button"
          onClick={jumpToBottom}
          title="回到底部"
          className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border px-3 py-1 text-2xs shadow-md transition-opacity"
          style={{
            backgroundColor: "#ffffff",
            borderColor: "#e7e5e4",
            color: "#6b7280",
          }}
        >
          ↓ 回到底部
        </button>
      )}
    </div>
  );
}

function Splitter({
  orientation,
  containerRef,
}: {
  orientation: "vertical" | "horizontal";
  containerRef: React.RefObject<HTMLDivElement>;
}): JSX.Element {
  const setChatPaneRatio = useUIStore((s) => s.setChatPaneRatio);
  const [dragging, setDragging] = useState(false);

  // 拖拽分隔条（2026-08-28）：同左/右侧栏 sash 方案 —— pointer capture +
  // 按容器内相对位置换算对话区占比；双击复位 0.5。
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    setDragging(true);
    // 可选链：部分环境（如 jsdom 测试）无 pointer capture API，不影响拖拽换算
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (!dragging) return;
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    if (orientation === "vertical") {
      if (rect.width <= 0) return;
      setChatPaneRatio((e.clientX - rect.left) / rect.width);
    } else {
      if (rect.height <= 0) return;
      setChatPaneRatio((e.clientY - rect.top) / rect.height);
    }
  };
  const onPointerUp = (): void => setDragging(false);

  return (
    <div
      role="separator"
      aria-orientation={orientation}
      title="拖动调节宽度，双击恢复均分"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onDoubleClick={() => setChatPaneRatio(0.5)}
      className="flex-shrink-0 transition-colors hover:bg-[#10a37f]/50"
      data-testid="center-splitter"
      style={{
        width: orientation === "vertical" ? 5 : "100%",
        height: orientation === "vertical" ? "100%" : 5,
        cursor: orientation === "vertical" ? "col-resize" : "row-resize",
        backgroundColor: dragging ? "rgba(16,163,127,0.5)" : "#e7e5e4",
      }}
    />
  );
}

/**
 * 思考动画（2026-08-05 / aicss 风格升级 2026-08-10）：执行中且 assistant 还没开始输出时，
 * 在消息流末尾显示 aicss 风格思考指示器（星芒 orb + 流光文字 + 跳动圆点），
 * 避免长时间无反馈显得卡死。
 * 2026-08-26 细化（用户反馈「思考中太宽泛」）：
 *   - 只有发起执行的页签才显示（切到别的页签不再假显思考态）；
 *   - 文案随执行阶段变化：等模型返回 / 工具调用中：某动作。
 */
function ThinkingIndicator({
  messages,
  tabId,
}: {
  messages: ChatMessageT[];
  tabId: string;
}): JSX.Element | null {
  // 2026-08-26 多会话并发：指示器只在本页签有 run 时显示，阶段文案按 run 取，
  // 多个会话同时跑互不串扰。
  const isBusy = useChatStore((s) => s.busyTabIds.includes(tabId));
  const runId = useChatStore((s) => s.tabRunIds[tabId]);
  const phaseInfo = useChatStore((s) => (runId ? s.runPhaseByRun[runId] : undefined));
  if (!isBusy) return null;
  const phase = phaseInfo?.phase ?? "model";
  const detail = phaseInfo?.detail ?? "";
  const last = messages[messages.length - 1];
  // assistant 已经开始输出正文 → 不再重复展示思考态（工具阶段照常展示）
  if (phase !== "tool" && last && last.role === "assistant" && last.content) return null;
  const label = phase === "tool" && detail ? `工具调用中：${detail}` : "等模型返回";
  return <AiThinkingIndicator label={label} />;
}

function SplitButton({
  active,
  onClick,
  label,
  title,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  title: string;
}): JSX.Element {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="rounded px-2 py-0.5"
      style={{
        backgroundColor: active ? "#10a37f" : "transparent",
        color: active ? "#ffffff" : "#6b7280",
        border: "1px solid #e7e5e4",
      }}
    >
      {label}
    </button>
  );
}
