/**
 * Phase 15 V0 · 前端预览组件单测（≥ 8 条）。
 *
 * 覆盖：DeviceModeToggle 切换回调 / 高亮、ZoomControl 五档 / 回调、
 * PreviewButton 启用禁用 / 点击触发 / 快捷键、HmrStatusBadge 三态。
 * Tauri `listen` 与 `invoke` 在测试里 mock 掉（不启动真实 Webview）。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const { listeners } = vi.hoisted(() => {
  const listeners = new Map<string, (e: { payload: unknown }) => void>();
  return { listeners };
});

const { invokeCalls, previewStartError } = vi.hoisted(() => {
  const invokeCalls: Array<{ cmd: string; args: Record<string, unknown> }> = [];
  // 非空时 preview_start mock 抛错（模拟后端 400：Node.js 缺失 / 白名单拒绝等）；
  // rejectUnlessAllowPath=true 时：仅 allow_path 为真的调用放行（#175 重试链路）
  const previewStartError: {
    message: string | null;
    rejectUnlessAllowPath: boolean;
  } = { message: null, rejectUnlessAllowPath: false };
  return { invokeCalls, previewStartError };
});

vi.mock("@tauri-apps/api/event", () => ({
  listen: async (event: string, handler: (e: { payload: unknown }) => void) => {
    listeners.set(event, handler);
    return () => {
      listeners.delete(event);
    };
  },
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string, args: Record<string, unknown>) => {
    invokeCalls.push({ cmd, args });
    if (cmd === "preview_start") {
      if (
        previewStartError.message &&
        !(previewStartError.rejectUnlessAllowPath && args.allowPath === true)
      ) {
        throw new Error(previewStartError.message);
      }
      return {
        id: "sess-1",
        project_path: args.projectPath,
        entry_file: args.entryFile ?? "",
        framework: "vue",
        port: 5173,
        url: "http://127.0.0.1:5173",
        status: "running",
        created_at: Date.now(),
        last_active_at: Date.now(),
        install_progress: 100,
      };
    }
    if (cmd === "preview_open_window") return "preview-sess-1";
    return {};
  },
}));

import { DeviceModeToggle } from "@/components/preview/DeviceModeToggle";
import { ZoomControl } from "@/components/preview/ZoomControl";
import { HmrStatusBadge } from "@/components/preview/HmrStatusBadge";
import { PreviewButton } from "@/components/preview/PreviewButton";
import { PreviewEventBridge } from "@/components/preview/PreviewEventBridge";
import { LivePreviewPanel } from "@/components/preview/LivePreviewPanel";
import { EVT } from "@/ipc/events";
import { usePreviewStore } from "@/store/previewStore";

function render(node: React.ReactElement): {
  root: Root;
  container: HTMLDivElement;
} {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(node);
  });
  return { root, container };
}

describe("DeviceModeToggle", () => {
  it("点击触发 onChange 回调", () => {
    const onChange = vi.fn();
    const { root, container } = render(
      <DeviceModeToggle value="desktop" onChange={onChange} />,
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(4);
    act(() => {
      (buttons[2] as HTMLButtonElement).click(); // mobile
    });
    expect(onChange).toHaveBeenCalledWith("mobile");
    root.unmount();
    container.remove();
  });

  it("当前模式高亮（aria-pressed）", () => {
    const { root, container } = render(
      <DeviceModeToggle value="tablet" onChange={() => undefined} />,
    );
    const buttons = [...container.querySelectorAll("button")];
    expect(buttons[1].getAttribute("aria-pressed")).toBe("true");
    expect(buttons[0].getAttribute("aria-pressed")).toBe("false");
    root.unmount();
    container.remove();
  });
});

describe("ZoomControl", () => {
  it("渲染五档并触发回调", () => {
    const onChange = vi.fn();
    const { root, container } = render(
      <ZoomControl value={100} onChange={onChange} />,
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(5);
    act(() => {
      (buttons[4] as HTMLButtonElement).click(); // 150%
    });
    expect(onChange).toHaveBeenCalledWith(150);
    root.unmount();
    container.remove();
  });

  it("当前缩放档高亮", () => {
    const { root, container } = render(
      <ZoomControl value={75} onChange={() => undefined} />,
    );
    const buttons = [...container.querySelectorAll("button")];
    expect(buttons[1].getAttribute("aria-pressed")).toBe("true");
    expect(buttons[0].textContent).toContain("50%");
    root.unmount();
    container.remove();
  });
});

describe("HmrStatusBadge", () => {
  it("connected 绿标", () => {
    const { root, container } = render(<HmrStatusBadge status="connected" />);
    expect(container.textContent).toContain("HMR connected");
    expect(container.querySelector(".bg-emerald-500")).toBeTruthy();
    root.unmount();
    container.remove();
  });

  it("disconnected 黄标", () => {
    const { root, container } = render(
      <HmrStatusBadge status="disconnected" />,
    );
    expect(container.textContent).toContain("HMR disconnected");
    expect(container.querySelector(".bg-amber-400")).toBeTruthy();
    root.unmount();
    container.remove();
  });
});

describe("PreviewButton", () => {
  afterEach(() => {
    invokeCalls.length = 0;
    previewStartError.message = null;
    previewStartError.rejectUnlessAllowPath = false;
    usePreviewStore.getState().setSessions([]);
    usePreviewStore.getState().setActiveSession(null);
  });

  it("非可预览文件 → 禁用", () => {
    const { root, container } = render(
      <PreviewButton currentFile="C:/proj/src/main.py" />,
    );
    const btn = container.querySelector(
      'button[data-testid="preview-button"]',
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    root.unmount();
    container.remove();
  });

  it("可预览文件 → 启用 + 点击触发 preview_start", async () => {
    const { root, container } = render(
      <PreviewButton currentFile="C:/proj/src/App.vue" />,
    );
    const btn = container.querySelector(
      'button[data-testid="preview-button"]',
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    await act(async () => {
      btn.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    const start = invokeCalls.find((c) => c.cmd === "preview_start");
    expect(start).toBeTruthy();
    const open = invokeCalls.find((c) => c.cmd === "preview_open_window");
    expect(open).toBeTruthy();
    expect(usePreviewStore.getState().activeSessionId).toBe("sess-1");
    root.unmount();
    container.remove();
  });

  it("已有同项目会话 → 聚焦而非新建", async () => {
    usePreviewStore.getState().upsertSession({
      id: "existing",
      project_path: "C:/proj/src",
      entry_file: "",
      framework: "vue",
      port: 5174,
      url: "http://127.0.0.1:5174",
      status: "running",
      created_at: 1,
      last_active_at: 1,
      install_progress: 100,
    });
    const beforeSessions = usePreviewStore.getState().sessions;
    expect(beforeSessions.some((s) => s.project_path === "C:/proj/src")).toBe(
      true,
    );
    const { root, container } = render(
      <PreviewButton currentFile="C:/proj/src/App.vue" />,
    );
    const btn = container.querySelector(
      'button[data-testid="preview-button"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(invokeCalls.some((c) => c.cmd === "preview_start")).toBe(false);
    expect(invokeCalls.some((c) => c.cmd === "preview_open_window")).toBe(true);
    expect(usePreviewStore.getState().activeSessionId).toBe("existing");
    root.unmount();
    container.remove();
  });

  it("preview_start 失败时不再静默，弹窗提示原因（BUGFIX #174）", async () => {
    previewStartError.message = "未检测到 Node.js，无法启动预览引擎";
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    const { root, container } = render(
      <PreviewButton currentFile="C:/proj/index.html" />,
    );
    const btn = container.querySelector(
      'button[data-testid="preview-button"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(alertSpy).toHaveBeenCalledTimes(1);
    expect(String(alertSpy.mock.calls[0][0])).toContain("启动预览失败");
    expect(String(alertSpy.mock.calls[0][0])).toContain("未检测到 Node.js");
    alertSpy.mockRestore();
    previewStartError.message = null;
    root.unmount();
    container.remove();
  });

  it("白名单拒绝 → 确认后带 allowPath 重试成功（BUGFIX #175）", async () => {
    previewStartError.message =
      "agent returned 400: 项目路径不在预览白名单内: D:/work/前端";
    previewStartError.rejectUnlessAllowPath = true;
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(true);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    const { root, container } = render(
      <PreviewButton currentFile="C:/proj/index.html" />,
    );
    const btn = container.querySelector(
      'button[data-testid="preview-button"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    // 首次不带 allowPath 被拒 → 弹确认框；确认后带 allowPath=true 重试成功
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(String(confirmSpy.mock.calls[0][0])).toContain("不在预览白名单");
    const starts = invokeCalls.filter((c) => c.cmd === "preview_start");
    expect(starts).toHaveLength(2);
    expect(starts[0].args.allowPath).toBe(false);
    expect(starts[1].args.allowPath).toBe(true);
    expect(usePreviewStore.getState().activeSessionId).toBe("sess-1");
    expect(alertSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
    alertSpy.mockRestore();
    root.unmount();
    container.remove();
  });

  it("白名单拒绝 → 用户取消确认则不重试不报错（BUGFIX #175）", async () => {
    previewStartError.message =
      "agent returned 400: 项目路径不在预览白名单内: D:/work/前端";
    previewStartError.rejectUnlessAllowPath = true;
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    const { root, container } = render(
      <PreviewButton currentFile="C:/proj/index.html" />,
    );
    const btn = container.querySelector(
      'button[data-testid="preview-button"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    // 只发了一次（取消后不重试），也不弹失败告警（用户主动取消）
    expect(invokeCalls.filter((c) => c.cmd === "preview_start")).toHaveLength(1);
    expect(alertSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
    alertSpy.mockRestore();
    root.unmount();
    container.remove();
  });
});

describe("PreviewEventBridge", () => {
  it("订阅三个 preview:// 事件通道", async () => {
    const { root } = render(<PreviewEventBridge />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(listeners.has(EVT.PREVIEW_HMR_CONNECTED)).toBe(true);
    expect(listeners.has(EVT.PREVIEW_HMR_DISCONNECTED)).toBe(true);
    expect(listeners.has(EVT.PREVIEW_BUILD_ERROR)).toBe(true);
    root.unmount();
  });

  it("HMR 事件更新 store 状态", async () => {
    const { root } = render(<PreviewEventBridge />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    act(() => {
      listeners.get(EVT.PREVIEW_HMR_CONNECTED)?.({
        payload: { session_id: "sess-a", status: "connected", timestamp: 1 },
      });
    });
    expect(usePreviewStore.getState().hmrStatus["sess-a"]).toBe("connected");
    root.unmount();
  });
});

describe("LivePreviewPanel 设备模式联动", () => {
  afterEach(() => {
    invokeCalls.length = 0;
    usePreviewStore.getState().setSessions([]);
    usePreviewStore.getState().setActiveSession(null);
    usePreviewStore.getState().setDeviceMode("desktop");
  });

  it("切换设备模式时调用 preview_resize_window（独立窗口联动）", async () => {
    usePreviewStore.getState().upsertSession({
      id: "sess-1",
      project_path: "C:/proj",
      entry_file: "src/App.vue",
      framework: "vue",
      port: 5173,
      url: "http://127.0.0.1:5173",
      status: "running",
      created_at: Date.now(),
      last_active_at: Date.now(),
      install_progress: 100,
    });
    const { root, container } = render(<LivePreviewPanel />);
    // 打开独立窗口（mock 返回 'preview-sess-1'）
    const openBtn = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("独立窗口"),
    );
    await act(async () => {
      openBtn?.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    // 设备模式按钮：🖥️ / 📱 / 📲 / ✂️ → 点第 3 个（手机）
    const modeButtons = [
      ...container.querySelectorAll(
        '[role="group"][aria-label="设备模式"] button',
      ),
    ];
    await act(async () => {
      (modeButtons[2] as HTMLButtonElement).click();
      await new Promise((r) => setTimeout(r, 0));
    });

    const resize = invokeCalls.find((c) => c.cmd === "preview_resize_window");
    expect(resize).toBeTruthy();
    expect(resize?.args).toMatchObject({
      sessionId: "sess-1",
      deviceMode: "mobile",
    });
    expect(usePreviewStore.getState().deviceMode).toBe("mobile");
    root.unmount();
    container.remove();
  });
});
