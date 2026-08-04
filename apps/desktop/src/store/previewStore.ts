/**
 * previewStore —— Phase 15 V0 前端实时预览引擎状态。
 *
 * 会话列表 / 当前选中会话 / 设备模式 / 缩放 / HMR 状态 / 编译错误。
 * 高频 HMR 事件只更新低频状态字段（session 状态机 + 徽章），
 * 不进入 React 渲染热路径（Vite 页面本身在 iframe / WebviewWindow 内）。
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

export type PreviewDeviceMode = "desktop" | "tablet" | "mobile" | "custom";
export type PreviewHmrStatus =
  "connected" | "disconnected" | "reconnecting" | "unknown";

export interface PreviewSession {
  id: string;
  project_path: string;
  entry_file: string;
  framework: "vue" | "react" | "svelte" | "html";
  port: number;
  url: string;
  status: "starting" | "running" | "installing" | "stopped" | "errored";
  created_at: number;
  last_active_at: number;
  pid?: number | null;
  install_progress: number;
  config_path?: string | null;
  error?: string | null;
}

export interface PreviewBuildError {
  session_id: string;
  error: string;
  file?: string | null;
  line?: number | null;
  column?: number | null;
  timestamp: number;
}

interface PreviewState {
  sessions: PreviewSession[];
  activeSessionId: string | null;
  deviceMode: PreviewDeviceMode;
  zoom: number; // 50/75/100/125/150
  hmrStatus: Record<string, PreviewHmrStatus>;
  buildErrors: Record<string, PreviewBuildError | null>;
  previewOpen: boolean; // 编辑器副栏是否显示预览面板

  upsertSession: (s: PreviewSession) => void;
  removeSession: (id: string) => void;
  setSessions: (list: PreviewSession[]) => void;
  setActiveSession: (id: string | null) => void;
  setDeviceMode: (m: PreviewDeviceMode) => void;
  setZoom: (z: number) => void;
  setHmrStatus: (sessionId: string, status: PreviewHmrStatus) => void;
  setBuildError: (sessionId: string, err: PreviewBuildError | null) => void;
  setPreviewOpen: (open: boolean) => void;
}

export const usePreviewStore = create<PreviewState>()(
  persist(
    (set) => ({
      sessions: [],
      activeSessionId: null,
      deviceMode: "desktop",
      zoom: 100,
      hmrStatus: {},
      buildErrors: {},
      previewOpen: false,

      upsertSession: (s) =>
        set((state) => {
          const idx = state.sessions.findIndex((x) => x.id === s.id);
          const sessions =
            idx >= 0
              ? state.sessions.map((x) => (x.id === s.id ? s : x))
              : [...state.sessions, s];
          return {
            sessions,
            activeSessionId: state.activeSessionId ?? s.id,
          };
        }),

      removeSession: (id) =>
        set((state) => ({
          sessions: state.sessions.filter((x) => x.id !== id),
          activeSessionId:
            state.activeSessionId === id ? null : state.activeSessionId,
        })),

      setSessions: (list) =>
        set((state) => {
          const activeStillExists = list.some(
            (x) => x.id === state.activeSessionId,
          );
          return {
            sessions: list,
            activeSessionId: activeStillExists
              ? state.activeSessionId
              : (list[0]?.id ?? null),
          };
        }),

      setActiveSession: (id) => set({ activeSessionId: id }),
      setDeviceMode: (m) => set({ deviceMode: m }),
      setZoom: (z) => set({ zoom: z }),
      setHmrStatus: (sessionId, status) =>
        set((state) => ({
          hmrStatus: { ...state.hmrStatus, [sessionId]: status },
        })),
      setBuildError: (sessionId, err) =>
        set((state) => ({
          buildErrors: { ...state.buildErrors, [sessionId]: err },
        })),
      setPreviewOpen: (open) => set({ previewOpen: open }),
    }),
    {
      name: "eaide-preview-store",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({
        deviceMode: s.deviceMode,
        zoom: s.zoom,
      }),
    },
  ),
);
