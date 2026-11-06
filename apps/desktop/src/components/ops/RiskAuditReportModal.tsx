/**
 * RiskAuditReportModal —— 专家验收前置「系统合规审核报告」弹窗（2026-11-05）。
 *
 * 材料交给 AI 专家验收前，先过一遍「审核专家 · 文档审核」模块（同一套分类 +
 * 分块风险分析 + 证据定位 + 知识库依据）。只有发现风险点时才弹本弹窗：
 *   风险等级/类型 → 风险描述 → 原文证据 → 处理建议 → 知识库依据。
 * 无风险不弹窗；已关闭的报告可从材料行「⚠ 合规风险 N」再次打开。
 */
import { useEffect, useMemo } from "react";
import type { OpsCaseFile, OpsRiskLevel } from "@/types/ops";
import { BookIcon, BulbIcon, CloseIcon, FlagIcon, WarningIcon } from "@/components/ui/Icons";

const EMPTY_FINDINGS: NonNullable<OpsCaseFile["risk_report"]>["findings"] = [];

const LEVEL_META: Record<OpsRiskLevel, { label: string; color: string; bg: string }> = {
  critical: { label: "严重", color: "#b3261e", bg: "#b3261e1f" },
  high: { label: "高危", color: "#cd3131", bg: "#cd31311f" },
  medium: { label: "中危", color: "#b25c1a", bg: "#b25c1a1f" },
  low: { label: "低危", color: "#059669", bg: "#0596691f" },
};

const RISK_TYPE_LABEL: Record<string, string> = {
  compliance: "合规",
  legal: "法律",
  data_security: "数据安全",
  financial: "财务",
};

export function RiskAuditReportModal({
  file,
  onClose,
}: {
  file: OpsCaseFile;
  onClose: () => void;
}): JSX.Element | null {
  const report = file.risk_report;
  // 兼容历史材料（risk_report 为 {}）：findings 缺失一律按空处理，不弹窗
  const findings = report?.findings ?? EMPTY_FINDINGS;

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const counts = useMemo(
    () => ({
      critical: findings.filter((f) => f.risk_level === "critical").length,
      high: findings.filter((f) => f.risk_level === "high").length,
      medium: findings.filter((f) => f.risk_level === "medium").length,
      low: findings.filter((f) => f.risk_level === "low").length,
    }),
    [findings],
  );

  if (!report || findings.length === 0) return null;
  const overall = report.overall_risk_level || "medium";
  const overallMeta = LEVEL_META[overall];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.45)" }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="flex max-h-[84vh] w-[720px] max-w-[92vw] flex-col rounded-lg shadow-2xl"
        style={{ backgroundColor: "#ffffff" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部：警告 + 风险总数 + 总体等级 + 关闭 */}
        <div
          className="flex items-center gap-2.5 border-b px-5 py-3"
          style={{ borderColor: "#e0e0e0", borderTop: `3px solid ${overallMeta.color}` }}
        >
          <span className="flex-shrink-0" style={{ color: overallMeta.color }} aria-hidden="true">
            <WarningIcon size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-ui font-semibold" style={{ color: "#1f1f1f" }}>
              系统合规审核发现 {findings.length} 项风险
            </div>
            <div className="mt-0.5 truncate text-[10px]" style={{ color: "#9ca3af" }}>
              材料：{file.file_name}
              {file.member_key ? ` · 出题专家：${file.member_key}` : ""}
              {report.basis === "vision" ? " · 基于图片视觉描述" : " · 基于材料文本/OCR"}
            </div>
          </div>
          <span
            className="flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold"
            style={{ backgroundColor: overallMeta.bg, color: overallMeta.color }}
          >
            总体 {overallMeta.label}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-ghost btn-icon"
            aria-label="关闭审核报告"
          >
            <CloseIcon size={13} />
          </button>
        </div>

        {/* 风险统计 */}
        <div
          className="flex flex-wrap items-center gap-2 border-b px-5 py-2"
          style={{ borderColor: "#f0efed" }}
        >
          {(Object.keys(LEVEL_META) as OpsRiskLevel[])
            .filter((lv) => counts[lv] > 0)
            .map((lv) => (
              <span
                key={lv}
                className="rounded px-2 py-0.5 text-[10px] font-bold"
                style={{ backgroundColor: LEVEL_META[lv].bg, color: LEVEL_META[lv].color }}
              >
                {LEVEL_META[lv].label} {counts[lv]}
              </span>
            ))}
          {report.doc_category && (
            <span className="text-[10px]" style={{ color: "#6b7280" }}>
              文档类型：{report.doc_category}
            </span>
          )}
          {typeof report.elapsed_ms === "number" && report.elapsed_ms > 0 && (
            <span className="ml-auto text-[10px]" style={{ color: "#9ca3af" }}>
              审核耗时 {(report.elapsed_ms / 1000).toFixed(1)}s
            </span>
          )}
        </div>

        {/* 风险明细：风险点 + 原文证据（可滚动） */}
        <div className="flex-1 space-y-2.5 overflow-y-auto px-5 py-3">
          {findings.map((f, idx) => {
            const meta = LEVEL_META[f.risk_level] ?? LEVEL_META.medium;
            return (
              <div
                key={f.finding_id || idx}
                className="rounded-lg border p-3"
                style={{ borderColor: "#e7e5e4", backgroundColor: "#fbfbfa" }}
              >
                <div className="flex items-center gap-1.5">
                  <span
                    className="flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold"
                    style={{ backgroundColor: meta.bg, color: meta.color }}
                  >
                    {meta.label}
                  </span>
                  <span
                    className="flex-shrink-0 rounded px-1.5 py-0.5 text-[10px]"
                    style={{ backgroundColor: "#f3f4f6", color: "#6b7280" }}
                  >
                    {RISK_TYPE_LABEL[f.risk_type] ?? f.risk_type}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-2xs font-semibold" style={{ color: "#1f1f1f" }}>
                    {idx + 1}. {f.title}
                  </span>
                </div>

                {f.description && (
                  <p className="mt-1.5 text-2xs" style={{ color: "#374151", lineHeight: 1.7 }}>
                    {f.description}
                  </p>
                )}

                {/* 原文证据：需求明确要求随报告展示，缺失时明确标注 */}
                <div
                  className="mt-2 rounded border-l-2 p-2"
                  style={{
                    borderColor: meta.color,
                    backgroundColor: "rgba(205,49,49,0.05)",
                  }}
                >
                  <p
                    className="flex items-center gap-1 text-[10px] font-bold"
                    style={{ color: meta.color }}
                  >
                    <FlagIcon size={11} />
                    原文证据
                  </p>
                  <p
                    className="mt-1 text-2xs whitespace-pre-wrap"
                    style={{ color: "#1f1f1f", lineHeight: 1.7 }}
                  >
                    {f.evidence_text || "（模型未给出可核对原文，请人工复核材料）"}
                  </p>
                </div>

                {f.suggestion && (
                  <p
                    className="mt-2 flex items-start gap-1 text-2xs"
                    style={{ color: "#065f46", lineHeight: 1.7 }}
                  >
                    <span className="mt-0.5 flex-shrink-0" aria-hidden="true">
                      <BulbIcon size={12} />
                    </span>
                    <span>处理建议：{f.suggestion}</span>
                  </p>
                )}

                {(f.kb_refs?.length ?? 0) > 0 && (
                  <div
                    className="mt-2 rounded border-l-2 p-2"
                    style={{ borderColor: "#b25c1a", backgroundColor: "#faf6f1" }}
                  >
                    <p
                      className="flex items-center gap-1 text-[10px] font-bold"
                      style={{ color: "#b25c1a" }}
                    >
                      <BookIcon size={11} />
                      依据（知识库）
                    </p>
                    {f.kb_refs!.map((ref, i) => (
                      <div key={`${f.finding_id}-kb-${i}`} className="mt-1">
                        <p className="text-[10px] font-semibold" style={{ color: "#1f1f1f" }}>
                          {ref.source}
                          {ref.heading ? ` · ${ref.heading}` : ""}
                        </p>
                        {ref.excerpt && (
                          <p className="mt-0.5 text-[10px]" style={{ color: "#616161", lineHeight: 1.6 }}>
                            {ref.excerpt}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* 底部 */}
        <div
          className="flex flex-shrink-0 items-center gap-2 border-t px-5 py-2.5"
          style={{ borderColor: "#e0e0e0" }}
        >
          <span className="min-w-0 flex-1 text-[10px]" style={{ color: "#9ca3af" }}>
            系统合规审核只做风险提示，不替代人工审批；验收结论以专家的审核意见为准。
          </span>
          <button type="button" onClick={onClose} className="btn btn-primary btn-sm">
            知道了
          </button>
        </div>
      </div>
    </div>
  );
}
