/**
 * officePreviewStore —— V9 Office 预览状态（OfficeCLI 渲染，2026-08-25）。
 *
 * 单例预览：同一时刻只预览一份文件（新请求覆盖旧会话）。
 * 瞬态状态不持久化（渲染产物在后端临时目录，Agent 重启即失效）。
 */
import { create } from "zustand";
import { convertFileSrc } from "@tauri-apps/api/core";

import { ipc } from "@/ipc/invoke";

export type OfficePreviewMode = "html" | "screenshot" | "markdown" | "text" | "pdf";
/** 预览来源：office = OfficeCLI 后端渲染；local-html = 直读本地 HTML；local-text = 直读本地 md/txt/csv；local-pdf = WebView 内嵌 PDF */
export type OfficePreviewSource = "office" | "local-html" | "local-text" | "local-pdf";

interface OfficePreviewState {
  open: boolean;
  loading: boolean;
  error: string | null;
  /** 源文件路径（展示用） */
  path: string | null;
  mode: OfficePreviewMode;
  source: OfficePreviewSource;
  sessionId: string | null;
  /** html 模式：渲染页全文（iframe srcDoc 展示） */
  html: string | null;
  /** screenshot 模式：PNG base64 */
  imageBase64: string | null;
  /** local-pdf 模式：经 convertFileSrc 得到的 asset URL（iframe src 展示） */
  pdfUrl: string | null;
  page: number;

  openPreview: (path: string, mode?: "html" | "screenshot", page?: number) => Promise<void>;
  /** 直读本地 HTML 文件预览（视觉演示稿；不产生后端会话） */
  openHtml: (path: string) => Promise<void>;
  /** 直读本地文本（md/txt/csv）预览：markdown 走渲染，其余走等宽纯文本（不产生后端会话） */
  openText: (path: string, mode?: "markdown" | "text") => Promise<void>;
  /** WebView 内嵌 PDF 预览（convertFileSrc → asset URL；无后端会话） */
  openPdf: (path: string) => void;
  /** 按来源分流刷新：local-html 重读文件，local-text 重读文本，local-pdf 重建 URL，office 重新渲染 */
  refresh: () => Promise<void>;
  close: () => void;
}

export const useOfficePreviewStore = create<OfficePreviewState>()((set, get) => ({
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

  openPreview: async (path, mode = "html", page = 1) => {
    // 覆盖旧会话前先停掉（best-effort，不阻塞新请求）
    const prev = get().sessionId;
    if (prev) {
      void ipc.officePreviewStop(prev).catch(() => undefined);
    }
    set({
      open: true,
      loading: true,
      error: null,
      path,
      mode,
      source: "office",
      sessionId: null,
      html: null,
      imageBase64: null,
      page,
    });
    try {
      const res = await ipc.officePreviewRender(path, mode, page);
      // 竞态保护：期间用户又发起了新预览，丢弃过期响应
      if (get().path !== path) return;
      set({
        loading: false,
        sessionId: res.session_id,
        html: res.html ?? null,
        imageBase64: res.image_base64 ?? null,
        page: res.page ?? page,
      });
    } catch (e) {
      if (get().path !== path) return;
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  openHtml: async (path) => {
    const prev = get().sessionId;
    if (prev) {
      void ipc.officePreviewStop(prev).catch(() => undefined);
    }
    set({
      open: true,
      loading: true,
      error: null,
      path,
      mode: "html",
      source: "local-html",
      sessionId: null,
      html: null,
      imageBase64: null,
      page: 1,
    });
    try {
      const content = await ipc.readTextFile(path);
      if (get().path !== path) return;
      set({ loading: false, html: content });
    } catch (e) {
      if (get().path !== path) return;
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  openText: async (path, mode = "markdown") => {
    const prev = get().sessionId;
    if (prev) {
      void ipc.officePreviewStop(prev).catch(() => undefined);
    }
    set({
      open: true,
      loading: true,
      error: null,
      path,
      mode,
      source: "local-text",
      sessionId: null,
      html: null,
      imageBase64: null,
      page: 1,
    });
    try {
      const content = await ipc.readTextFile(path);
      if (get().path !== path) return;
      // 文本复用 html 字段承载（面板按 source=local-text + mode 决定渲染方式）
      set({ loading: false, html: content });
    } catch (e) {
      if (get().path !== path) return;
      set({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  openPdf: (path) => {
    const prev = get().sessionId;
    if (prev) {
      void ipc.officePreviewStop(prev).catch(() => undefined);
    }
    // convertFileSrc 纯拼 URL（Windows → http://asset.localhost/…，其余 → asset://localhost/…），
    // 无 I/O；真正的文件读取由 WebView 经 asset 协议完成（受 tauri.conf 的 scope 限定）。
    let url: string;
    try {
      url = convertFileSrc(path);
    } catch (e) {
      set({
        open: true,
        loading: false,
        error: e instanceof Error ? e.message : String(e),
        path,
        mode: "pdf",
        source: "local-pdf",
        sessionId: null,
        html: null,
        imageBase64: null,
        pdfUrl: null,
        page: 1,
      });
      return;
    }
    set({
      open: true,
      loading: false,
      error: null,
      path,
      mode: "pdf",
      source: "local-pdf",
      sessionId: null,
      html: null,
      imageBase64: null,
      pdfUrl: url,
      page: 1,
    });
  },

  refresh: async () => {
    const { source, path, mode, page } = get();
    if (!path) return;
    if (source === "local-html") {
      await get().openHtml(path);
    } else if (source === "local-text") {
      await get().openText(path, mode === "text" ? "text" : "markdown");
    } else if (source === "local-pdf") {
      get().openPdf(path);
    } else {
      await get().openPreview(path, mode === "screenshot" ? "screenshot" : "html", page);
    }
  },

  close: () => {
    const sid = get().sessionId;
    if (sid) {
      void ipc.officePreviewStop(sid).catch(() => undefined);
    }
    set({
      open: false,
      loading: false,
      error: null,
      path: null,
      source: "office",
      sessionId: null,
      html: null,
      imageBase64: null,
      pdfUrl: null,
    });
  },
}));

/** 取小写扩展名（不含点；跨平台分隔符） */
function extOf(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? path;
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** OfficeCLI 可渲染的办公文档后缀（与后端 OFFICE_SUFFIXES 对齐） */
const OFFICE_EXTS = new Set(["docx", "xlsx", "pptx"]);
/** 走等宽纯文本预览的后缀（md/markdown 走 markdown 渲染） */
const TEXT_EXTS = new Set(["txt", "csv", "tsv", "log", "json", "yaml", "yml"]);

/**
 * 统一的本地文件预览入口（按扩展名分流）——供「知识库依据」与「已上传列表」点击复用：
 *   - docx/xlsx/pptx → OfficeCLI 渲染 HTML（openPreview）
 *   - md/markdown    → 轻量 Markdown 渲染（openText 'markdown'）
 *   - txt/csv/log 等 → 等宽纯文本（openText 'text'）
 *   - html/htm       → 直读本地 HTML（openHtml，沙箱 iframe）
 *   - pdf            → WebView 内嵌预览（openPdf，asset 协议 iframe）
 *   - doc 等其余     → 系统默认程序打开（无内置渲染器时的兜底）
 * 空路径直接忽略；系统打开失败时抛出由调用方提示。
 */
export async function previewLocalFile(path: string): Promise<void> {
  const p = (path || "").trim();
  if (!p) return;
  const store = useOfficePreviewStore.getState();
  const ext = extOf(p);
  if (OFFICE_EXTS.has(ext)) {
    await store.openPreview(p, "html");
    return;
  }
  if (ext === "md" || ext === "markdown") {
    await store.openText(p, "markdown");
    return;
  }
  if (TEXT_EXTS.has(ext)) {
    await store.openText(p, "text");
    return;
  }
  if (ext === "html" || ext === "htm") {
    await store.openHtml(p);
    return;
  }
  if (ext === "pdf") {
    store.openPdf(p); // WebView 内嵌预览（asset 协议）
    return;
  }
  // doc 等无内置渲染器：交系统默认程序打开
  await ipc.openWithDefault(p);
}
