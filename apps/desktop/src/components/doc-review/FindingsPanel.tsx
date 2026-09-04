import { useEffect, useState } from "react";
import { save } from "@tauri-apps/plugin-dialog";
import { ipc } from "@/ipc/invoke";
import { previewLocalFile } from "@/store/officePreviewStore";
import {
  useDocReviewStore,
  DOC_REVIEW_RISK_COLORS,
} from "@/store/docReviewStore";
import type { DocFinding } from "@eaide/shared-protocol";

/** 点击依据→预览命中的知识原文件（md/txt/html 内置预览，pdf/doc 等走系统默认程序）。 */
async function openRefFile(path: string): Promise<void> {
  try {
    await previewLocalFile(path);
  } catch (e) {
    window.alert(`打开依据原文件失败：${path}\n${String(e)}`);
  }
}

function KbRefBlock({ finding }: { finding: DocFinding }): JSX.Element | null {
  const refs = finding.kb_refs ?? [];
  if (refs.length === 0) return null;
  return (
    <div
      className="mt-2 rounded border-l-2 p-1.5"
      style={{ borderColor: "#b25c1a", backgroundColor: "#faf6f1" }}
    >
      <p className="text-2xs font-bold" style={{ color: "#b25c1a" }}>
        为什么有风险 · 知识库依据
      </p>
      {refs.map((ref, i) => (
        <div key={`${finding.finding_id}-kb-${i}`} className="mt-1.5">
          <p className="text-2xs font-semibold" style={{ color: "#1f1f1f" }}>
            {ref.file_path ? (
              <button
                type="button"
                className="cursor-pointer rounded underline decoration-dotted underline-offset-2 hover:text-[#0b6bcb]"
                style={{ color: "#1f1f1f" }}
                title="点击预览依据原文件"
                onClick={(e) => {
                  e.stopPropagation();
                  void openRefFile(ref.file_path);
                }}
              >
                📖 {ref.source}
              </button>
            ) : (
              <>📖 {ref.source}</>
            )}
            {" · "}
            {ref.heading}
          </p>
          {ref.excerpt && (
            <p
              className="mt-0.5 text-2xs"
              style={{ color: "#616161", lineHeight: 1.5 }}
            >
              {ref.excerpt}
            </p>
          )}
          {ref.matched_terms.length > 0 && (
            <p className="mt-0.5 flex flex-wrap gap-1">
              {ref.matched_terms.map((t) => (
                <span
                  key={t}
                  className="rounded px-1"
                  style={{
                    backgroundColor: "rgba(178,92,26,0.14)",
                    color: "#8a4a15",
                    fontSize: 10,
                  }}
                >
                  {t}
                </span>
              ))}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

/** 跟随鼠标的悬浮提示：点击可放大查看完整内容 */
function HoverHint({ x, y }: { x: number; y: number }): JSX.Element {
  return (
    <div
      className="pointer-events-none fixed z-50 rounded px-2 py-1 text-2xs shadow-md"
      style={{
        left: Math.min(x + 14, window.innerWidth - 160),
        top: y + 18,
        backgroundColor: "#1f1f1f",
        color: "#ffffff",
      }}
    >
      🔍 点击查看完整风险详情
    </div>
  );
}

/** 完整内容放大查看弹窗 */
function FindingDetailModal({
  finding,
  onClose,
}: {
  finding: DocFinding;
  onClose: () => void;
}): JSX.Element {
  const selectFinding = useDocReviewStore((s) => s.selectFinding);
  const colors = DOC_REVIEW_RISK_COLORS[finding.risk_level];

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const locateSource = (): void => {
    selectFinding(finding.finding_id);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.45)" }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="flex max-h-[80vh] w-[620px] max-w-[90vw] flex-col rounded-md shadow-2xl"
        style={{ backgroundColor: "#ffffff" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div
          className="flex items-center gap-2 border-b px-4 py-2.5"
          style={{ borderColor: "#e0e0e0" }}
        >
          <span
            className="rounded px-1.5 py-0.5 text-2xs font-bold"
            style={{ backgroundColor: colors.bg, color: colors.badge }}
          >
            {finding.risk_type} · {finding.risk_level}
          </span>
          <span className="flex-1 truncate text-ui font-semibold" style={{ color: "#1f1f1f" }}>
            {finding.title}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-1.5 text-ui"
            style={{ color: "#616161" }}
            aria-label="关闭"
          >
            ✕
          </button>
        </div>
        {/* 正文：完整展示，可滚动、可选中复制 */}
        <div
          className="flex-1 overflow-auto px-4 py-3"
          style={{ userSelect: "text" }}
        >
          <p className="text-2xs font-bold" style={{ color: "#616161" }}>
            风险描述
          </p>
          <p className="mt-1 text-ui" style={{ color: "#1f1f1f", lineHeight: 1.7 }}>
            {finding.description || "（无描述）"}
          </p>
          {finding.suggestion && (
            <>
              <p className="mt-3 text-2xs font-bold" style={{ color: "#059669" }}>
                💡 处理建议
              </p>
              <p className="mt-1 text-ui" style={{ color: "#1f1f1f", lineHeight: 1.7 }}>
                {finding.suggestion}
              </p>
            </>
          )}
          {finding.evidence_text && (
            <>
              <p className="mt-3 text-2xs font-bold" style={{ color: "#cd3131" }}>
                📌 原文证据
              </p>
              <div
                className="mt-1 rounded border-l-2 p-2 text-ui"
                style={{
                  borderColor: "#cd3131",
                  backgroundColor: "rgba(205,49,49,0.06)",
                  color: "#1f1f1f",
                  lineHeight: 1.7,
                }}
              >
                {finding.evidence_text}
              </div>
            </>
          )}
          {finding.rule_ref && (
            <p className="mt-3 text-2xs" style={{ color: "#616161" }}>
              规则依据：{finding.rule_ref}
            </p>
          )}
          <KbRefBlock finding={finding} />
        </div>
        {/* 底部操作 */}
        <div
          className="flex items-center justify-end gap-2 border-t px-4 py-2"
          style={{ borderColor: "#e0e0e0" }}
        >
          <button
            type="button"
            onClick={locateSource}
            className="rounded border px-3 py-1 text-2xs"
            style={{ borderColor: "#0b6bcb", color: "#0b6bcb", backgroundColor: "#ffffff" }}
          >
            📍 定位原文高亮
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded border px-3 py-1 text-2xs"
            style={{ borderColor: "#d4d4d4", color: "#616161", backgroundColor: "#ffffff" }}
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

export function FindingsPanel(): JSX.Element {
  const findings = useDocReviewStore((s) => s.findings);
  const detail = useDocReviewStore((s) => s.detail);
  const selectedDocId = useDocReviewStore((s) => s.selectedDocId);
  // 悬浮提示位置（null = 不显示）
  const [hint, setHint] = useState<{ x: number; y: number } | null>(null);
  // 放大查看的 finding（null = 未打开）
  const [detailFinding, setDetail] = useState<DocFinding | null>(null);
  const [exporting, setExporting] = useState(false);

  /** 导出 Word：full = 全文+批注，risks_only = 结构化风险报告 */
  const onExport = async (mode: "full" | "risks_only"): Promise<void> => {
    if (!selectedDocId) return;
    if (findings.length === 0) {
      alert("暂无可导出的风险点（分析可能未完成）");
      return;
    }
    const stem = (detail?.file_name ?? "审核结果").replace(/\.[^.]+$/, "");
    const suffix = mode === "full" ? "审核全文批注" : "审核风险报告";
    let picked: string | null = null;
    try {
      picked = await save({
        defaultPath: `${stem}_${suffix}.docx`,
        filters: [{ name: "Word 文档", extensions: ["docx"] }],
      });
    } catch {
      // 非 Tauri 环境（vitest）无对话框，静默降级
      return;
    }
    if (!picked) return;
    setExporting(true);
    try {
      const res = await ipc.docReviewExportWord(selectedDocId, mode, picked);
      alert(`导出成功：${res.path}`);
    } catch (err) {
      alert(`导出失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div
      className="flex h-full flex-col"
      style={{ backgroundColor: "#f3f3f3" }}
    >
      <div
        className="flex items-center gap-2 border-b px-3 py-2"
        style={{ borderColor: "#d4d4d4" }}
      >
        <span className="text-ui font-semibold" style={{ color: "#333" }}>
          风险发现（{findings.length}）
        </span>
        <span className="ml-auto flex gap-1">
          <button
            type="button"
            disabled={exporting}
            onClick={() => void onExport("risks_only")}
            title="只导出风险点 + 对应原文摘录，结构化报告"
            className="rounded border px-1.5 py-0.5 text-2xs"
            style={{ borderColor: "#0b6bcb", color: "#0b6bcb", backgroundColor: "#ffffff" }}
          >
            导出风险报告
          </button>
          <button
            type="button"
            disabled={exporting}
            onClick={() => void onExport("full")}
            title="导出全部原文，风险点以 Word 批注形式标注在对应位置"
            className="rounded border px-1.5 py-0.5 text-2xs"
            style={{ borderColor: "#0b6bcb", color: "#0b6bcb", backgroundColor: "#ffffff" }}
          >
            导出全文批注
          </button>
        </span>
      </div>
      <div
        className="flex-1 overflow-auto p-2"
        onMouseLeave={() => setHint(null)}
      >
        {findings.length === 0 && (
          <div className="p-3 text-2xs" style={{ color: "#616161" }}>
            暂无风险发现，或分析未完成
          </div>
        )}
        {findings.map((f) => (
          <div
            key={f.finding_id}
            id={`finding-card-${f.finding_id}`}
            role="button"
            tabIndex={0}
            onClick={() => setDetail(f)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") setDetail(f);
            }}
            onMouseMove={(e) => setHint({ x: e.clientX, y: e.clientY })}
            onMouseLeave={() => setHint(null)}
            className="mb-2 w-full cursor-pointer rounded border p-2 text-left"
            style={{ borderColor: "#d4d4d4", backgroundColor: "#ffffff" }}
          >
            <div className="flex items-center gap-1">
              <span
                className="text-2xs font-bold"
                style={{ color: DOC_REVIEW_RISK_COLORS[f.risk_level].badge }}
              >
                {f.risk_type} · {f.risk_level}
              </span>
              <span className="ml-auto text-2xs" style={{ color: "#9e9e9e" }}>
                🔍 点击放大
              </span>
            </div>
            <p className="mt-1 text-ui font-medium" style={{ color: "#1f1f1f" }}>
              {f.title}
            </p>
            <p
              className="finding-card-clamp mt-0.5 text-2xs"
              style={{ color: "#616161" }}
            >
              {f.description}
            </p>
            {f.suggestion && (
              <p
                className="finding-card-clamp mt-1 text-2xs"
                style={{ color: "#059669" }}
              >
                💡 {f.suggestion}
              </p>
            )}
            <KbRefBlock finding={f} />
          </div>
        ))}
      </div>
      {hint && <HoverHint x={hint.x} y={hint.y} />}
      {detailFinding && (
        <FindingDetailModal
          finding={detailFinding}
          onClose={() => setDetail(null)}
        />
      )}
    </div>
  );
}
