/**
 * OfficePreviewPanel —— V9 Office 文件预览浮层（OfficeCLI 渲染，2026-08-25）。
 *
 * 单例全屏浮层：文件树右键 / 聊天产物卡片触发 `openPreview` 后弹出。
 *   - html 模式：iframe srcDoc 展示渲染页（资源已内联；沙箱仅 allow-scripts）
 *   - screenshot 模式：base64 PNG 图片展示
 * 安全红线：渲染产物为本地静态内容，iframe 不给 allow-same-origin。
 */
import { useOfficePreviewStore } from "@/store/officePreviewStore";

export function OfficePreviewPanel(): JSX.Element | null {
  const { open, loading, error, path, mode, html, imageBase64, close, refresh } =
    useOfficePreviewStore();

  if (!open) return null;
  const fileName = path ? path.split(/[\\/]/).pop() ?? path : "";

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60"
      onClick={close}
    >
      <div
        className="flex h-[85vh] w-[82vw] flex-col overflow-hidden rounded border border-neutral-700 bg-neutral-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏 */}
        <div className="flex items-center justify-between gap-2 border-b border-neutral-800 px-3 py-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-[13px] font-semibold text-neutral-100">Office 预览</span>
            <span className="truncate text-[12px] text-neutral-400" title={path ?? ""}>
              {fileName}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button
              className="rounded px-2 py-1 text-[12px] text-neutral-300 hover:bg-neutral-800 disabled:opacity-50"
              disabled={loading || !path}
              onClick={() => void refresh()}
              title="重新渲染"
            >
              ⟳ 刷新
            </button>
            <button
              aria-label="关闭预览"
              className="rounded px-2 py-1 text-[12px] text-neutral-300 hover:bg-neutral-800"
              onClick={close}
            >
              ✕ 关闭
            </button>
          </div>
        </div>

        {/* 内容区 */}
        <div className="flex min-h-0 flex-1 items-stretch justify-center overflow-auto bg-neutral-950">
          {loading && (
            <div
              role="status"
              aria-live="polite"
              className="flex flex-1 flex-col items-center justify-center gap-3"
            >
              {/* 细环 spinner（与执行链路 / 文档审核同惯例） */}
              <span
                className="animate-spin-ring rounded-full"
                style={{
                  width: 28,
                  height: 28,
                  border: "3px solid #404040",
                  borderTopColor: "#3b82f6",
                }}
              />
              <span className="text-[13px] text-neutral-400">正在渲染文档，请稍候…</span>
              <span className="text-[11px] text-neutral-600">
                大文件 / 复杂排版渲染耗时较长，请勿关闭窗口
              </span>
            </div>
          )}
          {!loading && error && (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 px-8 text-center">
              <span className="text-[13px] text-red-400">预览失败</span>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-neutral-900 p-3 text-[12px] text-neutral-300">
                {error}
              </pre>
            </div>
          )}
          {!loading && !error && mode === "html" && html && (
            <iframe
              className="h-full w-full border-0 bg-white"
              sandbox="allow-scripts"
              srcDoc={html}
              title={fileName}
            />
          )}
          {!loading && !error && mode === "screenshot" && imageBase64 && (
            <img
              alt={fileName}
              className="m-auto max-h-full max-w-full object-contain"
              src={`data:image/png;base64,${imageBase64}`}
            />
          )}
          {!loading && !error && !html && !imageBase64 && (
            <div className="flex flex-1 items-center justify-center text-[13px] text-neutral-500">
              暂无预览内容
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
