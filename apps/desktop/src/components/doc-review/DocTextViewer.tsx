import { useEffect, useState } from "react";
import {
  useDocReviewStore,
  DOC_REVIEW_RISK_COLORS,
} from "@/store/docReviewStore";
import { splitBlockSegments } from "./highlight";

function scrollToFindingCard(findingId: string): void {
  const el = document.getElementById(`finding-card-${findingId}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  // 强调闪烁：粗红边框 + 呼吸光环（CSS .finding-card-flash，约 2.1s）
  el.classList.remove("finding-card-flash");
  void (el as HTMLElement).offsetWidth; // 强制重排，保证连续点击时动画重播
  el.classList.add("finding-card-flash");
  window.setTimeout(() => el.classList.remove("finding-card-flash"), 2200);
}

const ANALYZING_STAGE_LABELS: Record<string, string> = {
  queued: "排队中…",
  classifying: "文档分类中…",
  analyzing: "风险分析中…",
};

/** 分析中遮罩：文档模糊化 + 阶段提示 + 进度百分比 + 已用时 */
function AnalyzingOverlay({
  stage,
  progress,
}: {
  stage: string | null;
  progress: number | null;
}): JSX.Element {
  const pct =
    progress === null ? null : Math.max(0, Math.min(100, Math.round(progress * 100)));
  // 已用时计时器：分类/分析是真实模型调用，让用户知道进程活着
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    setElapsed(0);
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div
      className="absolute inset-0 z-10 flex items-center justify-center"
      style={{ backgroundColor: "rgba(255,255,255,0.35)" }}
    >
      <div
        className="flex w-64 flex-col items-center rounded-lg border px-8 py-6 shadow-lg"
        style={{ borderColor: "#d4d4d4", backgroundColor: "#ffffff" }}
      >
        <div
          className="animate-pulse text-2xl"
          style={{ color: "#b25c1a" }}
          aria-hidden
        >
          🔍
        </div>
        <p className="mt-2 text-ui font-semibold" style={{ color: "#1f1f1f" }}>
          正在分析，请稍后
          {pct !== null && (
            <span className="ml-1.5" style={{ color: "#b25c1a" }}>
              {pct}%
            </span>
          )}
        </p>
        <p className="mt-1 text-2xs" style={{ color: "#616161" }}>
          {stage && ANALYZING_STAGE_LABELS[stage]
            ? ANALYZING_STAGE_LABELS[stage]
            : "正在调用审核模型…"}
          <span className="ml-1.5">已用时 {elapsed}s</span>
        </p>
        <p className="mt-1 text-2xs" style={{ color: "#9e9e9e" }}>
          分类/分析为真实模型调用，耗时取决于模型与文档长度
        </p>
        {/* 进度条 */}
        <div
          className="mt-3 h-1.5 w-full overflow-hidden rounded"
          style={{ backgroundColor: "#e8e8e8" }}
        >
          <div
            className="h-full rounded"
            style={{
              width: pct === null ? "0%" : `${pct}%`,
              backgroundColor: "#b25c1a",
              transition: "width 0.6s ease",
            }}
          />
        </div>
      </div>
    </div>
  );
}

/** 分析完成横幅：总数 + 各危级统计（可关闭） */
function AnalysisDoneBanner(): JSX.Element | null {
  const summary = useDocReviewStore((s) => s.analysisSummary);
  const dismiss = useDocReviewStore((s) => s.dismissAnalysisSummary);
  if (!summary) return null;
  const chips: Array<{ label: string; count: number; color: string }> = [
    { label: "严重", count: summary.critical, color: "#b3261e" },
    { label: "高危", count: summary.high, color: "#cd3131" },
    { label: "中危", count: summary.medium, color: "#b25c1a" },
    { label: "低危", count: summary.low, color: "#059669" },
  ];
  return (
    <div
      className="mb-3 flex flex-wrap items-center gap-2 rounded border px-3 py-2"
      style={{ borderColor: "#c8e6c9", backgroundColor: "#f1f8f1" }}
    >
      <span className="text-ui font-semibold" style={{ color: "#059669" }}>
        ✓ 分析完成 · 共 {summary.total} 条风险点
      </span>
      {chips
        .filter((c) => c.count > 0)
        .map((c) => (
          <span
            key={c.label}
            className="rounded px-1.5 py-0.5 text-2xs font-bold"
            style={{ backgroundColor: `${c.color}1f`, color: c.color }}
          >
            {c.label} {c.count}
          </span>
        ))}
      {summary.total === 0 && (
        <span className="text-2xs" style={{ color: "#616161" }}>
          未发现风险
        </span>
      )}
      <button
        type="button"
        onClick={dismiss}
        className="ml-auto rounded px-1.5 text-2xs"
        style={{ color: "#616161" }}
        title="关闭"
      >
        ✕
      </button>
    </div>
  );
}

/** 分析失败横幅：展示错误原因 + 重试入口（此前错误无人渲染，导致静默失败） */
function AnalysisErrorBanner(): JSX.Element | null {
  const error = useDocReviewStore((s) => s.error);
  const analyzing = useDocReviewStore((s) => s.analyzing);
  const selectedDocId = useDocReviewStore((s) => s.selectedDocId);
  const analyze = useDocReviewStore((s) => s.analyze);
  if (!error || analyzing || !selectedDocId) return null;
  return (
    <div
      className="mb-3 flex flex-wrap items-center gap-2 rounded border px-3 py-2"
      style={{ borderColor: "#f5c6c6", backgroundColor: "#fdf1f1" }}
    >
      <span className="text-ui font-semibold" style={{ color: "#cd3131" }}>
        ✗ 分析失败：{error}
      </span>
      <button
        type="button"
        onClick={() => void analyze(selectedDocId)}
        className="ml-auto rounded border px-2 py-0.5 text-2xs"
        style={{
          borderColor: "#cd3131",
          color: "#cd3131",
          backgroundColor: "transparent",
          cursor: "pointer",
        }}
      >
        ↻ 重新分析
      </button>
    </div>
  );
}

export function DocTextViewer(): JSX.Element {
  const detail = useDocReviewStore((s) => s.detail);
  const findings = useDocReviewStore((s) => s.findings);
  const analyzing = useDocReviewStore((s) => s.analyzing);
  const progress = useDocReviewStore((s) => s.progress);

  // 交互节奏：分析开始后先正常预览文档 1.2s，再模糊化 + 遮罩
  const [blurred, setBlurred] = useState(false);
  useEffect(() => {
    if (!analyzing) {
      setBlurred(false);
      return;
    }
    setBlurred(false);
    const timer = setTimeout(() => setBlurred(true), 1200);
    return () => clearTimeout(timer);
  }, [analyzing]);

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
    <div className="relative h-full" style={{ backgroundColor: "#ffffff" }}>
      {analyzing && blurred && (
        <AnalyzingOverlay stage={detail.status} progress={progress} />
      )}
      <div
        className="h-full overflow-auto p-4"
        style={{
          filter: analyzing && blurred ? "blur(6px)" : "none",
          transition: "filter 0.4s ease",
          pointerEvents: analyzing && blurred ? "none" : "auto",
        }}
        aria-hidden={analyzing && blurred}
      >
        <AnalysisErrorBanner />
        <AnalysisDoneBanner />
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
                        onClick={() => scrollToFindingCard(seg.findingId!)}
                        title={
                          f
                            ? `${f.risk_type} · ${f.risk_level}：${f.title}`
                            : undefined
                        }
                        style={{
                          backgroundColor: f
                            ? DOC_REVIEW_RISK_COLORS[f.risk_level].bg
                            : "transparent",
                          borderRadius: 2,
                          cursor: "pointer",
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
    </div>
  );
}
