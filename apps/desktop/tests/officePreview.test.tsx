/**
 * V9 · Office 预览（OfficeCLI 渲染）前端单测。
 *
 * 覆盖：officePreviewStore 打开/关闭/错误态 + OfficePreviewPanel
 * iframe srcDoc 渲染 / 错误展示 / 关闭按钮。
 * Tauri invoke mock 掉（不启动真实 Agent / Rust 桥）。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const { invokeCalls, responder } = vi.hoisted(() => {
  const invokeCalls: Array<{ cmd: string; args: Record<string, unknown> }> = [];
  const responder = {
    render: async (args: Record<string, unknown>) => ({
      ok: true,
      session_id: "sess-office-1",
      mode: "html",
      html: "<html><body><h1>Q4 报告</h1></body></html>",
      html_url: "/office/preview/html/sess-office-1",
      ...(args.path === "C:/fail.pptx" ? {} : {}),
    }),
    readFile: async (_args: Record<string, unknown>) =>
      "<html><body><h1>本地演示稿</h1></body></html>",
  };
  return { invokeCalls, responder };
});

vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string, args: Record<string, unknown>) => {
    invokeCalls.push({ cmd, args });
    if (cmd === "office_preview_render") return responder.render(args);
    if (cmd === "office_preview_stop") return { ok: true, stopped: true };
    if (cmd === "read_text_file") return responder.readFile(args);
    return {};
  },
  convertFileSrc: (p: string) => `http://asset.localhost/${encodeURIComponent(p)}`,
}));

import { useOfficePreviewStore, previewLocalFile } from "@/store/officePreviewStore";
import { OfficePreviewPanel } from "@/components/office/OfficePreviewPanel";

function resetStore() {
  useOfficePreviewStore.setState({
    open: false,
    loading: false,
    error: null,
    path: null,
    mode: "html",
    source: "office",
    sessionId: null,
    html: null,
    imageBase64: null,
    pdfUrl: null,
    page: 1,
  });
  invokeCalls.length = 0;
}

afterEach(() => {
  resetStore();
});

describe("officePreviewStore", () => {
  it("openPreview 渲染成功后持有 html 与会话号", async () => {
    await act(async () => {
      await useOfficePreviewStore.getState().openPreview("C:/报告.docx");
    });
    const s = useOfficePreviewStore.getState();
    expect(s.open).toBe(true);
    expect(s.loading).toBe(false);
    expect(s.sessionId).toBe("sess-office-1");
    expect(s.html).toContain("Q4 报告");
    const call = invokeCalls.find((c) => c.cmd === "office_preview_render");
    expect(call?.args).toMatchObject({ path: "C:/报告.docx", mode: "html" });
  });

  it("close 停止会话并重置状态", async () => {
    await act(async () => {
      await useOfficePreviewStore.getState().openPreview("C:/报告.docx");
    });
    await act(async () => {
      useOfficePreviewStore.getState().close();
    });
    const s = useOfficePreviewStore.getState();
    expect(s.open).toBe(false);
    expect(s.sessionId).toBeNull();
    const stop = invokeCalls.find((c) => c.cmd === "office_preview_stop");
    expect(stop?.args).toMatchObject({ sessionId: "sess-office-1" });
  });

  it("渲染失败时记录错误信息", async () => {
    const original = responder.render;
    responder.render = async () => {
      throw new Error("officecli_not_installed: 请运行 fetch-officecli.ps1");
    };
    try {
      await act(async () => {
        await useOfficePreviewStore.getState().openPreview("C:/报告.docx");
      });
      const s = useOfficePreviewStore.getState();
      expect(s.open).toBe(true);
      expect(s.loading).toBe(false);
      expect(s.error).toContain("officecli_not_installed");
    } finally {
      responder.render = original;
    }
  });

  it("openHtml 直读本地文件渲染（不走 office 渲染，不产生后端会话）", async () => {
    await act(async () => {
      await useOfficePreviewStore.getState().openHtml("C:/演示稿.html");
    });
    const s = useOfficePreviewStore.getState();
    expect(s.open).toBe(true);
    expect(s.source).toBe("local-html");
    expect(s.sessionId).toBeNull();
    expect(s.html).toContain("本地演示稿");
    expect(invokeCalls.some((c) => c.cmd === "read_text_file")).toBe(true);
    expect(invokeCalls.some((c) => c.cmd === "office_preview_render")).toBe(false);
  });

  it("本地 html 关闭不调 office_preview_stop；刷新重新读文件", async () => {
    await act(async () => {
      await useOfficePreviewStore.getState().openHtml("C:/演示稿.html");
    });
    invokeCalls.length = 0;
    await act(async () => {
      await useOfficePreviewStore.getState().refresh();
    });
    // 刷新走重读文件而非 office 渲染
    expect(invokeCalls.some((c) => c.cmd === "read_text_file")).toBe(true);
    expect(invokeCalls.some((c) => c.cmd === "office_preview_render")).toBe(false);
    await act(async () => {
      useOfficePreviewStore.getState().close();
    });
    expect(useOfficePreviewStore.getState().open).toBe(false);
    expect(invokeCalls.some((c) => c.cmd === "office_preview_stop")).toBe(false);
  });

  it("openHtml 读文件失败时记录错误并保留重试入口", async () => {
    const original = responder.readFile;
    responder.readFile = async () => {
      throw new Error("read failed: 文件不存在");
    };
    try {
      await act(async () => {
        await useOfficePreviewStore.getState().openHtml("C:/不存在.html");
      });
      const s = useOfficePreviewStore.getState();
      expect(s.open).toBe(true);
      expect(s.loading).toBe(false);
      expect(s.error).toContain("read failed");
      expect(s.html).toBeNull();
    } finally {
      responder.readFile = original;
    }
  });
});

describe("previewLocalFile 分流", () => {
  it("pdf → WebView 内嵌（openPdf 设 asset URL）", async () => {
    await act(async () => {
      await previewLocalFile("C:/data/knowledge/files/x.pdf");
    });
    const s = useOfficePreviewStore.getState();
    expect(s.source).toBe("local-pdf");
    expect(s.mode).toBe("pdf");
    expect(s.pdfUrl).toContain("asset.localhost");
    expect(s.pdfUrl).toContain(encodeURIComponent("C:/data/knowledge/files/x.pdf"));
  });

  it("md → openText markdown（直读文本渲染）", async () => {
    await act(async () => {
      await previewLocalFile("C:/kb/制度.md");
    });
    const s = useOfficePreviewStore.getState();
    expect(s.source).toBe("local-text");
    expect(s.mode).toBe("markdown");
    expect(s.html).toContain("本地演示稿");
    const call = invokeCalls.find((c) => c.cmd === "read_text_file");
    expect(call?.args).toMatchObject({ path: "C:/kb/制度.md" });
  });

  it("docx → openPreview（OfficeCLI 渲染）", async () => {
    await act(async () => {
      await previewLocalFile("C:/报告.docx");
    });
    expect(useOfficePreviewStore.getState().source).toBe("office");
    expect(invokeCalls.some((c) => c.cmd === "office_preview_render")).toBe(true);
  });

  it("txt → openText text（等宽纯文本）", async () => {
    await act(async () => {
      await previewLocalFile("C:/notes.txt");
    });
    const s = useOfficePreviewStore.getState();
    expect(s.source).toBe("local-text");
    expect(s.mode).toBe("text");
  });

  it("doc → 系统默认程序打开（无内置渲染器兜底）", async () => {
    await act(async () => {
      await previewLocalFile("C:/legacy.doc");
    });
    expect(invokeCalls.some((c) => c.cmd === "open_with_default")).toBe(true);
  });

  it("空路径忽略", async () => {
    await act(async () => {
      await previewLocalFile("   ");
    });
    expect(useOfficePreviewStore.getState().open).toBe(false);
  });
});

describe("OfficePreviewPanel", () => {
  let container: HTMLDivElement;
  let root: Root;

  const mount = async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<OfficePreviewPanel />);
    });
  };

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it("未打开时不渲染任何内容", async () => {
    await mount();
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("加载中展示旋转动画 + 提示（不能空白等待）", async () => {
    useOfficePreviewStore.setState({
      open: true,
      loading: true,
      path: "C:/报告.docx",
    });
    await mount();
    const spinner = container.querySelector('[role="status"]');
    expect(spinner).not.toBeNull();
    expect(spinner?.querySelector(".animate-spin-ring")).not.toBeNull();
    expect(container.textContent).toContain("正在渲染文档");
  });

  it("html 模式渲染沙箱 iframe（srcDoc）", async () => {
    await act(async () => {
      await useOfficePreviewStore.getState().openPreview("C:/报告.docx");
    });
    await mount();
    const iframe = container.querySelector("iframe");
    expect(iframe).not.toBeNull();
    expect(iframe?.getAttribute("sandbox")).toBe("allow-scripts");
    expect(iframe?.getAttribute("srcdoc")).toContain("Q4 报告");
  });

  it("错误态展示错误信息", async () => {
    useOfficePreviewStore.setState({
      open: true,
      loading: false,
      error: "officecli_not_installed",
      path: "C:/报告.docx",
    });
    await mount();
    expect(container.textContent).toContain("officecli_not_installed");
    // 错误态不应残留加载动画，但提供重试出口（刷新按钮）
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it("点击关闭按钮调用 close（停止会话）", async () => {
    await act(async () => {
      await useOfficePreviewStore.getState().openPreview("C:/报告.docx");
    });
    await mount();
    const closeBtn = container.querySelector('[aria-label="关闭预览"]') as HTMLButtonElement;
    expect(closeBtn).not.toBeNull();
    await act(async () => {
      closeBtn.click();
    });
    expect(useOfficePreviewStore.getState().open).toBe(false);
    expect(invokeCalls.some((c) => c.cmd === "office_preview_stop")).toBe(true);
  });
});
