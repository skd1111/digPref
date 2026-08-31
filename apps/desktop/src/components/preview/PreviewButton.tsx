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

  /** 启动预览（BUGFIX #175：白名单拒绝 → 确认后带 allowPath 重试） */
  const startSession = useCallback(
    async (allowPath: boolean): Promise<void> => {
      if (!currentFile || !projectRoot) return;
      try {
        const session = await ipc.previewStart({
          projectPath: projectRoot,
          entryFile: currentFile,
          allowPath,
        });
        upsertSession(session);
        setActiveSession(session.id);
        await ipc.previewOpenWindow(session.id, session.url, "desktop");
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        // 白名单拒绝（#175）：用户确认加入后重试一次（入口仅限已导入工程）
        if (!allowPath && msg.includes("不在预览白名单")) {
          const ok = window.confirm(
            `该项目目录不在预览白名单内：\n${projectRoot}\n\n` +
              "是否允许预览该目录？（将持久化加入预览白名单）",
          );
          if (ok) {
            await startSession(true);
            return;
          }
          return;
        }
        // BUGFIX #174（2026-08-28）：此前 catch {} 静默吞错 —— 后端返回的明确错误
        //（Node.js 缺失 / node_modules 缺失 / Agent 离线等）用户完全看不到，
        // 表现为「点预览没反应」。改为弹窗提示失败原因。
        // eslint-disable-next-line no-console
        console.error("[PreviewButton] preview start failed:", e);
        window.alert(
          `启动预览失败：${msg}\n` +
            `项目目录：${projectRoot}\n` +
            "提示：预览引擎需要本机已安装 Node.js，且后端 Agent 服务在线。",
        );
      }
    },
    [currentFile, projectRoot, setActiveSession, upsertSession],
  );

  const startPreview = useCallback(async () => {
    if (!currentFile || !projectRoot) return;
    // 同项目已有会话 → 聚焦（不重新走启动链路）
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
    await startSession(false);
  }, [currentFile, projectRoot, sessions, setActiveSession, startSession]);

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
