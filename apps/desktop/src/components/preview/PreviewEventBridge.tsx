/**
 * PreviewEventBridge —— 订阅 preview://* SSE 事件（经 Tauri Event 转发），
 * 同步到 previewStore（HMR 状态 + 编译错误）。
 */
import { useEffect } from "react";

import { EVT, listen } from "@/ipc/events";
import { usePreviewStore } from "@/store/previewStore";
import type { PreviewBuildError } from "@/store/previewStore";

export function PreviewEventBridge() {
  useEffect(() => {
    const unlisteners: Array<() => void> = [];
    let alive = true;

    const subscribe = async () => {
      const onHmr =
        (status: "connected" | "disconnected" | "reconnecting") =>
        (e: { payload: { session_id?: string; status?: string } }) => {
          if (!e.payload.session_id) return;
          const st = (e.payload.status ?? status) as
            "connected" | "disconnected" | "reconnecting";
          usePreviewStore.getState().setHmrStatus(e.payload.session_id, st);
        };

      const un1 = await listen<{ session_id?: string; status?: string }>(
        EVT.PREVIEW_HMR_CONNECTED,
        onHmr("connected"),
      );
      const un2 = await listen<{ session_id?: string; status?: string }>(
        EVT.PREVIEW_HMR_DISCONNECTED,
        onHmr("disconnected"),
      );
      const un3 = await listen<PreviewBuildError>(
        EVT.PREVIEW_BUILD_ERROR,
        (e) => {
          const err = e.payload;
          if (!err?.session_id) return;
          usePreviewStore.getState().setBuildError(err.session_id, err);
        },
      );
      if (alive) unlisteners.push(un1, un2, un3);
    };

    subscribe().catch(() => undefined);
    return () => {
      alive = false;
      unlisteners.forEach((fn) => fn());
    };
  }, []);

  return null;
}
