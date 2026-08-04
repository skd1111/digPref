import { useEffect } from "react";
import { DocImportButton } from "./DocImportButton";
import {
  useDocReviewStore,
  DOC_REVIEW_RISK_COLORS,
} from "@/store/docReviewStore";

export function DocReviewList(): JSX.Element {
  const docs = useDocReviewStore((s) => s.docs);
  const selectedDocId = useDocReviewStore((s) => s.selectedDocId);
  const loadDocs = useDocReviewStore((s) => s.loadDocs);
  const open = useDocReviewStore((s) => s.open);

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

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
        {docs.map((d) => (
          <button
            key={d.doc_id}
            type="button"
            onClick={() => void open(d.doc_id)}
            className="w-full border-b px-3 py-2 text-left"
            style={{
              borderColor: "#d4d4d4",
              backgroundColor:
                selectedDocId === d.doc_id ? "#0e639c" : "transparent",
              color: selectedDocId === d.doc_id ? "#fff" : "#1f1f1f",
            }}
          >
            <div className="flex items-center justify-between text-2xs">
              <span className="font-mono uppercase">{d.format}</span>
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
            </div>
            <p className="text-ui">{d.file_name}</p>
            <p
              className="text-2xs"
              style={{
                color: selectedDocId === d.doc_id ? "#dcdcaa" : "#616161",
              }}
            >
              状态：{d.status}
            </p>
          </button>
        ))}
      </div>
    </div>
  );
}
