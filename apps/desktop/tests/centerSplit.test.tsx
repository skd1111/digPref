/**
 * BUGFIX #174（2026-08-28）回归测试：
 *   1. chat 与编辑器/文件区中间的分隔条此前是静态 4px 不可拖 ——
 *      现可拖拽调节占比（chatPaneRatio，0.15–0.85），双击复位 0.5。
 *   2. uiStore.setChatPaneRatio 越界夹取。
 *
 * CenterChatFlow 的重量级子组件（Monaco / ChatInput / TabBar 等）全部 mock，
 * 只验证分隔条拖拽换算占比的布局逻辑。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/components/chat/ChatMessage", () => ({
  ChatMessage: () => null,
  CHAT_SEND_EVENT: "chat-send-test",
}));
vi.mock("@/components/chat/ChatInput", () => ({ ChatInput: () => null }));
vi.mock("@/components/chat/TabBar", () => ({ TabBar: () => null }));
vi.mock("@/components/chat/ExecutionBlock", () => ({ ExecutionBlock: () => null }));
vi.mock("@/components/chat/ExecutionTree", () => ({ ExecutionTree: () => null }));
vi.mock("@/components/chat/AiStatus", () => ({ AiThinkingIndicator: () => null }));
vi.mock("@/components/biznav/ContextChip", () => ({ ContextChip: () => null }));
vi.mock("@/components/reqflow/ReqAlignmentBanner", () => ({
  ReqAlignmentBanner: () => null,
}));
vi.mock("@/components/editor/CodeEditorPane", () => ({
  CodeEditorPane: () => <div data-testid="code-editor" />,
}));
vi.mock("@/components/preview/LivePreviewPanel", () => ({
  LivePreviewPanel: () => null,
}));
vi.mock("@/components/preview/PreviewButton", () => ({
  PreviewButton: () => null,
}));
vi.mock("@/lib/executionGrouping", () => ({
  groupExecutionSteps: () => [],
}));

import { CenterChatFlow } from "@/layouts/CenterChatFlow";
import { useUIStore } from "@/store/uiStore";
import { useCodeNavStore } from "@/store/codeNavStore";
import { useChatStore } from "@/store/chatStore";

beforeEach(() => {
  localStorage.clear();
  useUIStore.setState({ editorSplit: "vertical", chatPaneRatio: 0.5 });
  useCodeNavStore.setState({
    openFiles: [{ path: "/proj/a.js", content: "x", language: "javascript" }],
    activeFilePath: "/proj/a.js",
  });
  useChatStore.setState({
    tabs: [{ id: "t1", title: "会话", messages: [] }],
    activeTabId: "t1",
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("uiStore.setChatPaneRatio 夹取", () => {
  it("越界值被夹到 0.15–0.85，合法值原样写入", () => {
    useUIStore.getState().setChatPaneRatio(0.01);
    expect(useUIStore.getState().chatPaneRatio).toBe(0.15);
    useUIStore.getState().setChatPaneRatio(0.99);
    expect(useUIStore.getState().chatPaneRatio).toBe(0.85);
    useUIStore.getState().setChatPaneRatio(0.7);
    expect(useUIStore.getState().chatPaneRatio).toBe(0.7);
  });
});

describe("中央分隔条拖拽（BUGFIX #174）", () => {
  it("左右拆分时拖动分隔条按容器内位置换算对话区占比", () => {
    // jsdom 无布局：给容器伪造 1000×600 的 boundingRect
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      right: 1000,
      bottom: 600,
      width: 1000,
      height: 600,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });

    render(<CenterChatFlow />);
    const splitter = screen.getByTestId("center-splitter");
    expect(splitter.getAttribute("role")).toBe("separator");

    act(() => {
      fireEvent.pointerDown(splitter, { pointerId: 1, clientX: 500 });
    });
    act(() => {
      fireEvent.pointerMove(splitter, { pointerId: 1, clientX: 700 });
    });
    act(() => {
      fireEvent.pointerUp(splitter, { pointerId: 1 });
    });
    // 700 / 1000 = 0.7
    expect(useUIStore.getState().chatPaneRatio).toBeCloseTo(0.7, 5);

    // 双击复位均分
    fireEvent.doubleClick(splitter);
    expect(useUIStore.getState().chatPaneRatio).toBe(0.5);
  });

  it("超出边界时夹取到 0.15–0.85", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      right: 1000,
      bottom: 600,
      width: 1000,
      height: 600,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });

    render(<CenterChatFlow />);
    const splitter = screen.getByTestId("center-splitter");
    act(() => {
      fireEvent.pointerDown(splitter, { pointerId: 1, clientX: 500 });
    });
    act(() => {
      fireEvent.pointerMove(splitter, { pointerId: 1, clientX: 99999 });
    });
    expect(useUIStore.getState().chatPaneRatio).toBe(0.85);
  });
});
