/**
 * 审核模式页签保持（BUGFIX #200）。
 *
 * 背景：设置页在 WorkspaceLayout 里走 `<Outlet />` 分支，会把 AuditDashboard 整体卸载。
 * 页签状态原先存在组件 useState 里 → 从知识库页点「设置」再关闭，回来必被打回「审批工作台」。
 * 现改为存 uiStore（持久化），本用例锁住「卸载重挂载后仍是原页签」。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuditDashboard } from "@/views/AuditDashboard";
import { useUIStore } from "@/store/uiStore";

vi.mock("@/ipc/invoke", () => ({ ipc: {} }));
vi.mock("@/components/doc-review/KbPanel", () => ({
  KbPanel: () => <div>KB-PANEL</div>,
}));
vi.mock("@/views/DocReviewDashboard", () => ({
  DocReviewDashboard: () => <div>DOC-PANEL</div>,
}));

describe("审核模式页签保持", () => {
  beforeEach(() => {
    useUIStore.setState({ auditTab: "kb" });
  });
  afterEach(() => {
    cleanup();
  });

  it("组件卸载（进设置页）后重新挂载，仍停在知识库", () => {
    const first = render(<AuditDashboard />);
    expect(screen.getByText("KB-PANEL")).toBeTruthy();
    first.unmount();

    render(<AuditDashboard />);
    expect(screen.getByText("KB-PANEL")).toBeTruthy();
    expect(screen.queryByText("DOC-PANEL")).toBeNull();
  });

  it("点页签写入 uiStore（不依赖组件本地 state）", () => {
    render(<AuditDashboard />);
    fireEvent.click(screen.getByText("文档审核"));
    expect(useUIStore.getState().auditTab).toBe("doc");
    expect(screen.getByText("DOC-PANEL")).toBeTruthy();

    fireEvent.click(screen.getByText("知识库"));
    expect(useUIStore.getState().auditTab).toBe("kb");
    expect(screen.getByText("KB-PANEL")).toBeTruthy();
  });
});
