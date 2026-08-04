/**
 * LivePreviewPanel —— 预览主面板。
 *
 * 两种模式（用户偏好持久化在 previewStore）：
 *   - window（默认）：独立 WebviewWindow 主路径；面板显示控制条 + 占位
 *   - inline（兜底）：iframe 嵌入 + device 宽度 + transform scale 缩放
 *
 * 安全红线（设计 §5.2）：iframe sandbox 仅 allow-scripts allow-same-origin
 * （Vite 服务同源 127.0.0.1）。
 */
import { useEffect, useMemo, useState } from "react";
import { clsx } from "clsx";

import { ipc } from "@/ipc/invoke";
import { usePreviewStore } from "@/store/previewStore";
import { BuildErrorPanel } from "./BuildErrorPanel";
import { DeviceModeToggle, type PreviewDeviceMode } from "./DeviceModeToggle";
import { HmrStatusBadge } from "./HmrStatusBadge";
import { SessionList } from "./SessionList";
import { ZoomControl } from "./ZoomControl";

const DEVICE_WIDTH: Record<PreviewDeviceMode, number | null> = {
  desktop: null,
  tablet: 768,
  mobile: 375,
  custom: 480,
};

export function LivePreviewPanel() {
  const {
    sessions,
    activeSessionId,
    deviceMode,
    zoom,
    hmrStatus,
    buildErrors,
    setActiveSession,
    setDeviceMode,
    setZoom,
  } = usePreviewStore();

  const active = useMemo(
    () => sessions.find((s) => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );
  const [windowOpened, setWindowOpened] = useState(false);

  useEffect(() => {
    if (active) setWindowOpened(false);
  }, [active?.id]);

  const openWindow = async () => {
    if (!active) return;
    try {
      await ipc.previewOpenWindow(active.id, active.url, deviceMode);
      setWindowOpened(true);
    } catch {
      setWindowOpened(false);
    }
  };

  // 设备模式切换：内嵌模式改 iframe 宽度；独立窗口模式同步 resize WebviewWindow
  const changeDeviceMode = (mode: PreviewDeviceMode) => {
    setDeviceMode(mode);
    if (active && windowOpened) {
      void ipc.previewResizeWindow(active.id, mode).catch(() => undefined);
    }
  };

  const deviceWidth = DEVICE_WIDTH[deviceMode];
  const status = active ? (hmrStatus[active.id] ?? "unknown") : "unknown";
  const buildError = active ? (buildErrors[active.id] ?? null) : null;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-neutral-900 text-neutral-200">
      {/* 顶部控制条 */}
      <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-3 py-2">
        <SessionList
          sessions={sessions}
          activeId={activeSessionId}
          onSelect={setActiveSession}
          onNew={openWindow}
        />
        <div className="flex items-center gap-3">
          {active && <HmrStatusBadge status={status} />}
          <DeviceModeToggle value={deviceMode} onChange={changeDeviceMode} />
          <ZoomControl value={zoom} onChange={setZoom} />
        </div>
      </div>

      {/* 预览区 */}
      <div className="relative flex-1 overflow-auto bg-neutral-950 p-4">
        {!active && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-neutral-500">
            <div className="text-2xl">▶️</div>
            <div className="text-sm">
              在编辑器中打开 .vue / .tsx / .html 文件并点击预览按钮
            </div>
          </div>
        )}

        {active && (
          <div
            className={clsx(
              "mx-auto flex h-full flex-col items-center justify-start",
            )}
            style={{
              width: deviceWidth ? `${deviceWidth}px` : "100%",
              maxWidth: "100%",
            }}
          >
            <div
              className="relative w-full flex-1 overflow-hidden rounded-lg border border-neutral-800 bg-white"
              data-testid="preview-viewport"
            >
              <iframe
                key={active.id}
                src={active.url}
                title={`Preview ${active.id}`}
                sandbox="allow-scripts allow-same-origin"
                style={{
                  width: deviceWidth ? `${deviceWidth}px` : "100%",
                  height: "100%",
                  transform: `scale(${zoom / 100})`,
                  transformOrigin: "top left",
                  border: 0,
                }}
              />
              {buildError && (
                <BuildErrorPanel
                  error={buildError.error}
                  file={buildError.file ?? null}
                  line={buildError.line ?? null}
                  column={buildError.column ?? null}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* 状态栏 */}
      {active && (
        <div className="flex items-center gap-3 border-t border-neutral-800 px-3 py-1.5 text-[11px] text-neutral-500">
          <span>
            {active.framework} · 端口 {active.port}
          </span>
          <span className="truncate">{active.url}</span>
          <button
            type="button"
            onClick={openWindow}
            className="ml-auto rounded px-2 py-0.5 text-xs text-sky-400 hover:bg-neutral-800"
          >
            {windowOpened ? "聚焦独立窗口" : "在独立窗口打开"}
          </button>
        </div>
      )}
    </div>
  );
}
