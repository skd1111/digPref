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
import { useEffect, useState } from "react";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { TabBar } from "@/components/chat/TabBar";
import { ExecutionBlock } from "@/components/chat/ExecutionBlock";
import { useChatStore, type ChatTab } from "@/store/chatStore";
import { useUIStore } from "@/store/uiStore";
import { useCodeNavStore } from "@/store/codeNavStore";
import { ContextChip } from "@/components/biznav/ContextChip";
import { SkillRoutingBadge } from "@/components/skills/SkillRoutingBadge";
import { CodeEditorPane } from "@/components/editor/CodeEditorPane";
import { LivePreviewPanel } from "@/components/preview/LivePreviewPanel";
import { PreviewButton } from "@/components/preview/PreviewButton";
import { usePreviewStore } from "@/store/previewStore";

export function CenterChatFlow(): JSX.Element {
  const tabs = useChatStore((s) => s.tabs);
  const activeId = useChatStore((s) => s.activeTabId);
  const secondaryId = useUIStore((s) => s.secondaryTabId);
  const setSecondaryId = useUIStore((s) => s.setSecondaryTabId);
  const editorSplit = useUIStore((s) => s.editorSplit);
  const setEditorSplit = useUIStore((s) => s.setEditorSplit);
  const previewOpen = usePreviewStore((s) => s.previewOpen);
  const setPreviewOpen = usePreviewStore((s) => s.setPreviewOpen);
  const activeFilePath = useCodeNavStore((s) => s.activeFilePath);

  const activeTab = tabs.find((t) => t.id === activeId);
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
        style={{ backgroundColor: "#f3f3f3", borderColor: "#e0e0e0" }}
      >
        {/* Phase 15 V0：▶️ 预览按钮（编辑器工具栏） */}
        <PreviewButton currentFile={activeFilePath} />
        <span
          className="mx-1 h-4 w-px"
          style={{ backgroundColor: "#d4d4d4" }}
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
        className="flex flex-1 overflow-hidden"
        style={{
          flexDirection: editorSplit === "horizontal" ? "column" : "row",
        }}
      >
        <Pane tab={activeTab ?? null} flex={1} />
        {editorSplit && (
          <>
            <Splitter
              orientation={
                editorSplit === "vertical" ? "vertical" : "horizontal"
              }
            />
            {hasCodeFiles ? (
              previewOpen ? (
                <LivePreviewPanel />
              ) : (
                <CodeEditorPane />
              )
            ) : (
              <Pane
                tab={secondaryTab ?? null}
                flex={1}
                onChangeTab={(id) => setSecondaryId(id)}
                tabs={tabs}
              />
            )}
          </>
        )}
      </div>

      <div
        className="border-t px-4 py-3"
        style={{ borderColor: "#d4d4d4", backgroundColor: "#f3f3f3" }}
      >
        <SkillRoutingBadge />
        <ContextChip />
        <ChatInput />
      </div>
    </div>
  );
}

// ---- 子组件 --------------------------------------------------------------

function Pane({
  tab,
  flex,
  tabs,
  onChangeTab,
}: {
  tab: ChatTab | null;
  flex: number;
  tabs?: ChatTab[];
  onChangeTab?: (id: string) => void;
}): JSX.Element {
  const [paneHighlight, setPaneHighlight] = useState(false);

  return (
    <div
      className="flex flex-col overflow-hidden"
      style={{
        flex,
        backgroundColor: "#ffffff",
        outline: paneHighlight ? "1px solid #007acc" : "none",
      }}
      onMouseEnter={() => setPaneHighlight(true)}
      onMouseLeave={() => setPaneHighlight(false)}
    >
      {/* 副栏 tab 选择器 */}
      {tabs && onChangeTab && (
        <div
          className="flex h-[26px] items-center gap-1 border-b px-2"
          style={{ backgroundColor: "#f3f3f3", borderColor: "#d4d4d4" }}
        >
          <span className="text-2xs text-fg-muted">副栏:</span>
          <select
            value={tab?.id ?? ""}
            onChange={(e) => onChangeTab(e.target.value)}
            className="rounded px-1 py-0.5 text-2xs outline-none"
            style={{
              backgroundColor: "#ececec",
              color: "#333333",
              border: "1px solid #d4d4d4",
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

      <div className="flex-1 overflow-auto px-6 py-4">
        {tab?.messages.length === 0 || !tab ? (
          <div className="flex h-full flex-col items-center justify-center text-fg-muted">
            <div className="mb-2 text-ui-lg font-semibold text-fg">EAIDE</div>
          </div>
        ) : (
          tab.messages.map((m) =>
            m.role === "system" && m.kind === "execution" ? (
              <ExecutionBlock key={m.id} message={m} />
            ) : (
              <ChatMessage key={m.id} message={m} />
            ),
          )
        )}
      </div>
    </div>
  );
}

function Splitter({
  orientation,
}: {
  orientation: "vertical" | "horizontal";
}): JSX.Element {
  return (
    <div
      className="flex-shrink-0"
      style={{
        width: orientation === "vertical" ? 4 : "100%",
        height: orientation === "vertical" ? "100%" : 4,
        backgroundColor: "#e0e0e0",
      }}
    />
  );
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
        backgroundColor: active ? "#007acc" : "transparent",
        color: active ? "#ffffff" : "#616161",
        border: "1px solid #d4d4d4",
      }}
    >
      {label}
    </button>
  );
}
