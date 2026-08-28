/**
 * 顶层 App 组件 —— ErrorBoundary + 路由。
 *
 * 借鉴 VSCode 的 ErrorBoundary 模式：
 *   渲染异常不会导致整个窗口白屏，而是显示可恢复的错误界面。
 */
import { Component, type ReactNode } from "react";
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { useAgentStream } from "@/hooks/useAgentStream";
import { useAgentHealth } from "@/hooks/useAgentHealth";
import { PreviewEventBridge } from "@/components/preview/PreviewEventBridge";
import { OfficePreviewPanel } from "@/components/office/OfficePreviewPanel";

// ---- ErrorBoundary（防止渲染异常导致白屏）-----------------------------------

interface ErrorBoundaryState {
  error: Error | null;
}

class ErrorBoundary extends Component<
  { children: ReactNode },
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="flex h-screen w-screen items-center justify-center bg-bg-base text-fg">
          <div className="max-w-md rounded border border-accent-danger bg-bg-panel p-6 text-center shadow-lg">
            <h1 className="mb-2 text-lg font-bold text-accent-danger">
              应用发生错误
            </h1>
            <p className="mb-4 text-sm text-fg-muted">
              渲染过程中遇到未预期的异常。请尝试重启应用。
            </p>
            <pre className="mb-4 overflow-auto rounded bg-bg-code p-3 text-left text-xs text-fg-muted">
              {this.state.error.message}
            </pre>
            <button
              onClick={() => window.location.reload()}
              className="rounded bg-accent px-4 py-2 text-sm font-semibold text-white hover:opacity-90"
            >
              重新加载
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// ---- App -------------------------------------------------------------------

export function App(): JSX.Element {
  // 挂载 Agent 流订阅：把 Rust SSE 桥发出的 agent://* 事件路由到
  // chatStore / traceStore。此前该 hook 从未被任何组件调用（打包时被
  // tree-shaking 掉），导致发消息后事件无人接收、UI 无响应。
  useAgentStream();
  // Agent /health 轮询 → uiStore.agentStatus（修复状态栏永远显示「未连接」的 bug）
  useAgentHealth();
  // Phase 15 V0：订阅 preview://* HMR / 编译错误事件（三处同步的 TS 端）

  return (
    <ErrorBoundary>
      <PreviewEventBridge />
      <RouterProvider router={router} />
      {/* V9 Office 预览浮层（文件树右键 / 聊天产物卡片触发） */}
      <OfficePreviewPanel />
    </ErrorBoundary>
  );
}
