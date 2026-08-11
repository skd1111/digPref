import { useEffect } from "react";
import { DocImportButton } from "./DocImportButton";
import {
  useDocReviewStore,
  DOC_REVIEW_RISK_COLORS,
} from "@/store/docReviewStore";

/** 后端运行状态 → 中文展示 */
const STATUS_LABELS: Record<string, string> = {
  none: "未分析",
  queued: "排队中",
  classifying: "分类中",
  analyzing: "分析中",
  done: "已完成",
  failed: "分析失败",
};

export function DocReviewList(): JSX.Element {
  const docs = useDocReviewStore((s) => s.docs);
  const selectedDocId = useDocReviewStore((s) => s.selectedDocId);
  const loadDocs = useDocReviewStore((s) => s.loadDocs);
  const open = useDocReviewStore((s) => s.open);
  const remove = useDocReviewStore((s) => s.remove);
  const analyzing = useDocReviewStore((s) => s.analyzing);
  const analyzeDoc = useDocReviewStore((s) => s.analyzeDoc);

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

  const onDelete = async (docId: string, fileName: string): Promise<void> => {
    if (selectedDocId === docId && analyzing) {
      alert("该文档正在分析中，请等待完成后再删除");
      return;
    }
    if (!window.confirm(`确定删除文档「${fileName}」？\n将同时删除其分析记录，磁盘源文件不受影响。`)) {
      return;
    }
    const res = await remove(docId);
    if (!res.ok) alert(`删除失败：${res.error ?? "未知错误"}`);
  };

  return (
    <div
      className="flex h-full flex-col"
      style={{ backgroundColor: "#f3f3f3" }}
    >
      <div
        className="flex items-center justify-between border-b px-3 py-2"
        style={{ borderColor: "#d4d4d4" }}
      >
        <h3 className="text-ui font-semibold" style={{ color: "#333" }}>
          文档列表
        </h3>
        <DocImportButton />
      </div>
      <div className="flex-1 overflow-auto">
        {docs.length === 0 && (
          <div className="p-4 text-2xs" style={{ color: "#616161" }}>
            暂无文档，点击“导入文档”开始
          </div>
        )}
        {docs.map((d) => {
          const selected = selectedDocId === d.doc_id;
          return (
            <div
              key={d.doc_id}
              role="button"
              tabIndex={0}
              onClick={() => void open(d.doc_id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") void open(d.doc_id);
              }}
              className="group w-full cursor-pointer border-b px-3 py-2 text-left"
              style={{
                borderColor: "#d4d4d4",
                backgroundColor: selected ? "#0e639c" : "transparent",
                color: selected ? "#fff" : "#1f1f1f",
              }}
            >
              <div className="flex items-center justify-between text-2xs">
                <span className="font-mono uppercase">{d.format}</span>
                <span className="flex items-center gap-1.5">
                  {d.overall_risk_level && (
                    <span
                      style={{
                        background:
                          DOC_REVIEW_RISK_COLORS[d.overall_risk_level].badge,
                        color: "#fff",
                        padding: "1px 6px",
                        borderRadius: 3,
                      }}
                    >
                      {d.overall_risk_level}
                    </span>
                  )}
                  <button
                    type="button"
                    title="删除该文档及分析记录"
                    onClick={(e) => {
                      e.stopPropagation();
                      void onDelete(d.doc_id, d.file_name);
                    }}
                    className="rounded px-1 leading-none opacity-0 transition-opacity group-hover:opacity-100"
                    style={{
                      color: selected ? "#ffb3b3" : "#cd3131",
                      fontSize: 13,
                    }}
                  >
                    ✕
                  </button>
                </span>
              </div>
              <p className="text-ui">{d.file_name}</p>
              <div className="flex items-center justify-between">
                <p
                  className="text-2xs"
                  style={{
                    color: selected ? "#dcdcaa" : "#616161",
                  }}
                >
                  状态：{STATUS_LABELS[d.status] ?? d.status}
                </p>
                {/* 分析 / 重新分析：导入后不再自动分析，手动触发 */}
                {analyzing && selected ? (
                  <span
                    className="text-2xs"
                    style={{ color: selected ? "#dcdcaa" : "#b25c1a" }}
                  >
                    分析中…
                  </span>
                ) : (
                  <button
                    type="button"
                    title={d.status === "done" ? "重新分析该文档" : "分析该文档"}
                    onClick={(e) => {
                      e.stopPropagation();
                      void analyzeDoc(d.doc_id);
                    }}
                    className="rounded border px-1.5 py-0.5 text-2xs"
                    style={{
                      borderColor: selected ? "#dcdcaa" : "#0e639c",
                      color: selected ? "#dcdcaa" : "#0e639c",
                      backgroundColor: "transparent",
                      cursor: "pointer",
                    }}
                  >
                    {d.status === "done" ? "↻ 重新分析" : "▶ 分析"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
