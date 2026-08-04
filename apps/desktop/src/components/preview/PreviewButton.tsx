/**
 * PreviewButton —— Monaco 工具栏 ▶️ 预览按钮。
 *
 * 状态联动：当前文件是 .vue/.tsx/.jsx/.html/.svelte 时高亮；
 * 点击 → POST /preview/start + 打开独立预览窗口。
 * 已启动同一项目 → 聚焦现有窗口。
 */
import { useCallback, useEffect } from "react";
import { clsx } from "clsx";

import { ipc } from "@/ipc/invoke";
import { usePreviewStore } from "@/store/previewStore";
import { isPreviewableFile, useProjectRoot } from "@/hooks/useProjectRoot";

export function PreviewButton({
  currentFile,
  disabled = false,
}: {
  currentFile?: string | null;
  disabled?: boolean;
}) {
  const projectRoot = useProjectRoot(currentFile);
  const { sessions, upsertSession, setActiveSession } = usePreviewStore();
  const previewable = isPreviewableFile(currentFile) && projectRoot !== null;

  const startPreview = useCallback(async () => {
    if (!currentFile || !projectRoot) return;
    // 同项目已有会话 → 聚焦
    const existing = sessions.find(
      (s) => s.project_path.replace(/[\\/]+$/, "") === projectRoot,
    );
    if (
      existing &&
      existing.status !== "errored" &&
      existing.status !== "stopped"
    ) {
      setActiveSession(existing.id);
      await ipc.previewOpenWindow(existing.id, existing.url, "desktop");
      return;
    }
    try {
      const session = await ipc.previewStart({
        projectPath: projectRoot,
        entryFile: currentFile,
      });
      upsertSession(session);
      setActiveSession(session.id);
      await ipc.previewOpenWindow(session.id, session.url, "desktop");
    } catch {
      // 后端会返回明确错误（Node.js 缺失 / node_modules 缺失等）
      // 这里静默失败，错误提示由主面板状态栏承接
    }
  }, [currentFile, projectRoot, sessions, setActiveSession, upsertSession]);

  // 快捷键：Ctrl+Shift+P 启动预览 / Ctrl+Shift+R 强制刷新（设计 §3.1）
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (!(e.ctrlKey || e.metaKey) || !e.shiftKey) return;
      if (e.key === "P" || e.key === "p") {
        e.preventDefault();
        void startPreview();
      } else if (e.key === "R" || e.key === "r") {
        e.preventDefault();
        const active = usePreviewStore.getState().activeSessionId;
        if (active) void ipc.previewReload(active);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [startPreview]);

  return (
    <button
      type="button"
      title="启动预览 (Ctrl+Shift+P)"
      aria-label="启动预览"
      disabled={disabled || !previewable}
      onClick={startPreview}
      className={clsx(
        "flex h-6 items-center gap-1 rounded px-2 text-xs font-medium transition-colors",
        previewable && !disabled
          ? "text-emerald-400 hover:bg-emerald-500/10"
          : "cursor-not-allowed text-neutral-600",
      )}
      data-testid="preview-button"
    >
      ▶️ 预览
    </button>
  );
}
