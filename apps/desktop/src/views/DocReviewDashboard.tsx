import { useEffect, useState } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { DocReviewList } from "@/components/doc-review/DocReviewList";
import { DocTextViewer } from "@/components/doc-review/DocTextViewer";
import { FindingsPanel } from "@/components/doc-review/FindingsPanel";
import { useDocReviewStore } from "@/store/docReviewStore";

/** 与后端 parser 支持格式保持一致 */
const SUPPORTED_EXTENSIONS = new Set([
  "pdf",
  "docx",
  "doc",
  "txt",
  "md",
  "csv",
  "html",
  "htm",
  "xlsx",
  "pptx",
]);

function extOf(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

export function DocReviewDashboard(): JSX.Element {
  const register = useDocReviewStore((s) => s.register);
  const open = useDocReviewStore((s) => s.open);
  const importingFile = useDocReviewStore((s) => s.importingFile);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let cancelled = false;

    const handleDrop = async (paths: string[]): Promise<void> => {
      const supported = paths.filter((p) => SUPPORTED_EXTENSIONS.has(extOf(p)));
      if (supported.length === 0) {
        alert("不支持的文件类型（支持 pdf/docx/doc/txt/md/csv/html/xlsx/pptx）");
        return;
      }
      const failed: string[] = [];
      let firstDocId: string | null = null;
      for (const p of supported) {
        const res = await register(p);
        if (!res.ok) {
          failed.push(`${p.split(/[\\/]/).pop()}: ${res.error ?? "未知错误"}`);
        } else if (firstDocId === null && res.doc_id) {
          firstDocId = res.doc_id;
        }
      }
      if (failed.length > 0) {
        alert(`部分文件导入失败：\n${failed.join("\n")}`);
      }
      // 自动打开第一个成功导入的文档并触发分析
      if (firstDocId) {
        void open(firstDocId);
      }
    };

    try {
      const webview = getCurrentWebview();
      void webview
        .onDragDropEvent((event) => {
          const payload = event.payload;
          if (payload.type === "enter" || payload.type === "over") {
            setDragging(true);
          } else if (payload.type === "leave") {
            setDragging(false);
          } else if (payload.type === "drop") {
            setDragging(false);
            void handleDrop(payload.paths ?? []);
          }
        })
        .then((fn) => {
          if (cancelled) {
            fn();
          } else {
            unlisten = fn;
          }
        });
    } catch {
      // 非 Tauri 环境（vitest / 浏览器）无 webview，静默降级
    }

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [register, open]);

  return (
    <div className="relative flex h-full">
      {dragging && (
        <div
          className="absolute inset-0 z-20 flex items-center justify-center"
          style={{
            backgroundColor: "rgba(14,99,156,0.12)",
            border: "2px dashed #0e639c",
          }}
        >
          <div
            className="rounded-lg px-8 py-5 text-ui font-semibold shadow-lg"
            style={{ backgroundColor: "#ffffff", color: "#0e639c" }}
          >
            📄 松开鼠标，导入文档进行审核
          </div>
        </div>
      )}
      {importingFile && (
        <div
          className="absolute inset-0 z-30 flex items-center justify-center"
          style={{ backgroundColor: "rgba(255,255,255,0.55)" }}
        >
          <div
            className="flex flex-col items-center gap-3 rounded-lg px-8 py-6 shadow-lg"
            style={{ backgroundColor: "#ffffff", color: "#0e639c" }}
          >
            <span
              className="animate-spin-ring rounded-full"
              style={{
                width: 26,
                height: 26,
                border: "3px solid #0e639c33",
                borderTopColor: "#0e639c",
              }}
            />
            <div className="text-ui font-semibold">正在导入「{importingFile}」…</div>
            <div className="text-2xs" style={{ color: "#616161" }}>
              解析文档中，.doc 格式需经 Word 转换，可能需要数十秒，请稍候
            </div>
          </div>
        </div>
      )}
      <div className="doc-review-dashboard flex h-full w-full">
        <div
          className="flex-shrink-0 border-r"
          style={{ width: 280, borderColor: "#d4d4d4" }}
        >
          <DocReviewList />
        </div>
        <div className="flex-1 overflow-hidden">
          <DocTextViewer />
        </div>
        <div
          className="flex-shrink-0 border-l"
          style={{ width: 320, borderColor: "#d4d4d4" }}
        >
          <FindingsPanel />
        </div>
      </div>
    </div>
  );
}
