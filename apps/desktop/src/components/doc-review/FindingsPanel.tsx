import {
  useDocReviewStore,
  DOC_REVIEW_RISK_COLORS,
} from "@/store/docReviewStore";

export function FindingsPanel(): JSX.Element {
  const findings = useDocReviewStore((s) => s.findings);
  const selectFinding = useDocReviewStore((s) => s.selectFinding);

  return (
    <div
      className="flex h-full flex-col"
      style={{ backgroundColor: "#f3f3f3" }}
    >
      <div
        className="border-b px-3 py-2 text-ui font-semibold"
        style={{ borderColor: "#d4d4d4", color: "#333" }}
      >
        风险发现（{findings.length}）
      </div>
      <div className="flex-1 overflow-auto p-2">
        {findings.length === 0 && (
          <div className="p-3 text-2xs" style={{ color: "#616161" }}>
            暂无风险发现，或分析未完成
          </div>
        )}
        {findings.map((f) => (
          <button
            key={f.finding_id}
            type="button"
            onClick={() => selectFinding(f.finding_id)}
            className="mb-2 w-full rounded border p-2 text-left"
            style={{ borderColor: "#d4d4d4", backgroundColor: "#ffffff" }}
          >
            <span
              className="text-2xs font-bold"
              style={{ color: DOC_REVIEW_RISK_COLORS[f.risk_level].badge }}
            >
              {f.risk_type} · {f.risk_level}
            </span>
            <p className="mt-1 text-ui" style={{ color: "#1f1f1f" }}>
              {f.title}
            </p>
            <p className="mt-0.5 text-2xs" style={{ color: "#616161" }}>
              {f.description}
            </p>
          </button>
        ))}
      </div>
    </div>
  );
}
