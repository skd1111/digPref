import {
  useDocReviewStore,
  DOC_REVIEW_RISK_COLORS,
} from "@/store/docReviewStore";
import { splitBlockSegments } from "./highlight";

export function DocTextViewer(): JSX.Element {
  const detail = useDocReviewStore((s) => s.detail);
  const findings = useDocReviewStore((s) => s.findings);

  if (!detail) {
    return (
      <div
        className="flex h-full items-center justify-center text-2xs"
        style={{ color: "#616161" }}
      >
        ← 选择文档后展示内容
      </div>
    );
  }
  return (
    <div
      className="h-full overflow-auto p-4"
      style={{ backgroundColor: "#ffffff" }}
    >
      <div className="mb-3 flex flex-wrap gap-2">
        {(["critical", "high", "medium", "low"] as const).map((lv) => (
          <span
            key={lv}
            className="rounded px-2 py-0.5 text-2xs"
            style={{ backgroundColor: DOC_REVIEW_RISK_COLORS[lv].bg }}
          >
            {lv}
          </span>
        ))}
      </div>
      {detail.pages.map((page) => (
        <section key={page.page_no} className="mb-6">
          <h4
            className="mb-2 text-2xs font-bold uppercase"
            style={{ color: "#616161" }}
          >
            第 {page.page_no} 页
          </h4>
          {page.blocks.map((block) => {
            const segs = splitBlockSegments(block, findings);
            return (
              <p
                key={block.block_id}
                className="mb-3 whitespace-pre-wrap"
                style={{ lineHeight: 1.8 }}
              >
                {segs.map((seg, i) => {
                  if (!seg.findingId) return <span key={i}>{seg.text}</span>;
                  const f = findings.find(
                    (x) => x.finding_id === seg.findingId,
                  );
                  return (
                    <mark
                      key={i}
                      id={`finding-${seg.findingId}`}
                      style={{
                        backgroundColor: f
                          ? DOC_REVIEW_RISK_COLORS[f.risk_level].bg
                          : "transparent",
                        borderRadius: 2,
                      }}
                    >
                      {seg.text}
                    </mark>
                  );
                })}
              </p>
            );
          })}
        </section>
      ))}
    </div>
  );
}
