/**
 * officePreviewStore —— V9 Office 预览状态（OfficeCLI 渲染，2026-08-25）。
 *
 * 单例预览：同一时刻只预览一份文件（新请求覆盖旧会话）。
 * 瞬态状态不持久化（渲染产物在后端临时目录，Agent 重启即失效）。
 */
import { create } from "zustand";

import { ipc } from "@/ipc/invoke";

export type OfficePreviewMode = "html" | "screenshot";
/** 预览来源：office = OfficeCLI 后端渲染；local-html = 直读本地 HTML（视觉演示稿） */
export type OfficePreviewSource = "office" | "local-html";

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
  page: number;

  openPreview: (path: string, mode?: OfficePreviewMode, page?: number) => Promise<void>;
  /** 直读本地 HTML 文件预览（视觉演示稿；不产生后端会话） */
  openHtml: (path: string) => Promise<void>;
  /** 按来源分流刷新：local-html 重读文件，office 重新渲染 */
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

  refresh: async () => {
    const { source, path, mode, page } = get();
    if (!path) return;
    if (source === "local-html") {
      await get().openHtml(path);
    } else {
      await get().openPreview(path, mode, page);
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
    });
  },
}));
