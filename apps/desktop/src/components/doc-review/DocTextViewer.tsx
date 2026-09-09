import { useEffect, useMemo, useRef, useState } from "react";
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

import type {
  DocFinding,
  DocPage,
  DocPosition,
  DocRiskLevel,
} from "@eaide/shared-protocol";

/** 根据 position 计算行号（页内行号，基于 block 文本换行符） */
export function computeLineNumber(
  pages: DocPage[],
  positions: DocPosition[],
): { pageNo: number; line: number }[] {
  if (!positions.length) return [];
  const results: { pageNo: number; line: number }[] = [];
  for (const pos of positions) {
    const page = pages.find((p) => p.page_no === pos.page_no);
    if (!page) continue;
    const block = page.blocks.find((b) => b.block_id === pos.block_id);
    if (!block) continue;
    const offsetInBlock = pos.start - block.start;
    const textBefore = block.text.slice(0, Math.max(0, offsetInBlock));
    const line = (textBefore.match(/\n/g) || []).length + 1;
    results.push({ pageNo: pos.page_no, line });
  }
  return results;
}

/** 跳转到指定块并闪烁强调 */
export function jumpToBlock(blockId: string): void {
  const el = document.getElementById(`block-${blockId}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("finding-mark-flash");
  void (el as HTMLElement).offsetWidth;
  el.classList.add("finding-mark-flash");
  window.setTimeout(() => el.classList.remove("finding-mark-flash"), 2200);
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

/** 风险维度悬浮导航条：sticky 置顶 + 悬浮展开每条风险预览 + 点击定位 */
function RiskBar({
  findings,
}: {
  findings: DocFinding[];
}): JSX.Element | null {
  const pages = useDocReviewStore((s) => s.detail?.pages ?? []);
  const selectFinding = useDocReviewStore((s) => s.selectFinding);

  const byLevel = useMemo(() => {
    const m: Record<string, DocFinding[]> = {};
    for (const f of findings) {
      (m[f.risk_level] ??= []).push(f);
    }
    return m;
  }, [findings]);

  if (findings.length === 0) return null;

  const LEVELS: { key: DocRiskLevel; label: string; color: string }[] = [
    { key: "critical", label: "严重", color: "#b3261e" },
    { key: "high", label: "高危", color: "#cd3131" },
    { key: "medium", label: "中危", color: "#b25c1a" },
    { key: "low", label: "低危", color: "#059669" },
  ];

  return (
    <div
      className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b px-4 py-2"
      style={{
        backgroundColor: "#ffffff",
        borderColor: "#e0e0e0",
        boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
      }}
    >
      <span className="text-2xs font-semibold" style={{ color: "#616161" }}>
        风险概览
      </span>
      {LEVELS.filter((l) => (byLevel[l.key]?.length ?? 0) > 0).map((lv) => (
        <RiskBadge
          key={lv.key}
          label={lv.label}
          color={lv.color}
          findings={byLevel[lv.key] ?? []}
          pages={pages}
          selectFinding={selectFinding}
        />
      ))}
      <span className="ml-auto text-2xs" style={{ color: "#9e9e9e" }}>
        共 {findings.length} 条
      </span>
    </div>
  );
}

/** 单个风险维度徽章 + 悬浮预览列表 */
function RiskBadge({
  label,
  color,
  findings,
  pages,
  selectFinding,
}: {
  label: string;
  color: string;
  findings: DocFinding[];
  pages: DocPage[];
  selectFinding: (id: string | null) => void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [popupPos, setPopupPos] = useState<{ left: number; top: number } | null>(
    null,
  );
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const badgeRef = useRef<HTMLButtonElement>(null);

  const startClose = (): void => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setOpen(false), 220);
  };
  const cancelClose = (): void => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  const onEnter = (): void => {
    cancelClose();
    if (badgeRef.current) {
      const rect = badgeRef.current.getBoundingClientRect();
      const left = Math.min(rect.left, window.innerWidth - 300);
      setPopupPos({ left, top: rect.bottom + 4 });
    }
    setOpen(true);
  };

  const onItemClick = (f: DocFinding): void => {
    setOpen(false);
    scrollToFindingCard(f.finding_id);
    if (f.positions.length > 0) {
      jumpToBlock(f.positions[0].block_id);
    }
    selectFinding(f.finding_id);
  };

  return (
    <div onMouseEnter={onEnter} onMouseLeave={startClose}>
      <button
        ref={badgeRef}
        type="button"
        className="flex cursor-pointer items-center gap-1 rounded px-2.5 py-1 text-2xs font-bold transition-shadow hover:shadow-sm"
        style={{ backgroundColor: `${color}1a`, color }}
      >
        {label}
        <span
          className="flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold"
          style={{ backgroundColor: color, color: "#fff" }}
        >
          {findings.length}
        </span>
      </button>

      {open && popupPos && (
        <div
          onMouseEnter={cancelClose}
          onMouseLeave={startClose}
          className="fixed z-50 max-h-[320px] w-[280px] overflow-auto rounded-md border shadow-xl"
          style={{
            left: popupPos.left,
            top: popupPos.top,
            backgroundColor: "#ffffff",
            borderColor: "#d4d4d4",
          }}
        >
          <div
            className="sticky top-0 border-b px-3 py-1.5 text-2xs font-semibold"
            style={{
              backgroundColor: "#fafafa",
              borderColor: "#e8e8e8",
              color,
            }}
          >
            {label}风险 · {findings.length} 条
          </div>
          {findings.map((f) => {
            const locs = computeLineNumber(pages, f.positions);
            const locStr =
              locs.length > 0
                ? `P${locs[0].pageNo} L${locs[0].line}`
                : "";
            const preview =
              f.title.length > 22
                ? `${f.title.slice(0, 22)}…`
                : f.title;
            return (
              <div
                key={f.finding_id}
                role="button"
                tabIndex={0}
                onClick={() => onItemClick(f)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onItemClick(f);
                }}
                className="cursor-pointer border-b px-3 py-2 last:border-b-0 hover:bg-[#f5f5f5]"
                style={{ borderColor: "#f0f0f0" }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span
                    className="flex-1 truncate text-2xs"
                    style={{ color: "#1f1f1f" }}
                    title={f.title}
                  >
                    {preview}
                  </span>
                  {locStr && (
                    <span
                      className="shrink-0 font-mono text-[10px]"
                      style={{ color: "#9e9e9e" }}
                    >
                      {locStr}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** 分析完成弹窗：风险统计 + 耗时明细，用户点「确定」后关闭 */
function AnalysisDoneDialog(): JSX.Element | null {
  const summary = useDocReviewStore((s) => s.analysisSummary);
  const dismiss = useDocReviewStore((s) => s.dismissAnalysisSummary);
  if (!summary) return null;

  const chips: Array<{ label: string; count: number; color: string }> = [
    { label: "严重", count: summary.critical, color: "#b3261e" },
    { label: "高危", count: summary.high, color: "#cd3131" },
    { label: "中危", count: summary.medium, color: "#b25c1a" },
    { label: "低危", count: summary.low, color: "#059669" },
  ];
  const t = summary.timings;
  const fmt = (ms: number): string =>
    ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.35)" }}
      onClick={dismiss}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-[380px] max-w-[90vw] rounded-lg shadow-2xl"
        style={{ backgroundColor: "#ffffff" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div
          className="flex items-center gap-2 border-b px-5 py-3"
          style={{ borderColor: "#e0e0e0" }}
        >
          <span className="text-xl" style={{ color: "#059669" }}>
            ✓
          </span>
          <span className="text-ui font-semibold" style={{ color: "#1f1f1f" }}>
            分析完成 · 共 {summary.total} 条风险点
          </span>
        </div>

        {/* 风险统计 */}
        <div className="flex flex-wrap items-center gap-2 px-5 py-3">
          {chips
            .filter((c) => c.count > 0)
            .map((c) => (
              <span
                key={c.label}
                className="rounded px-2 py-0.5 text-2xs font-bold"
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
        </div>

        {/* 耗时明细 */}
        {t && (
          <div
            className="mx-5 mb-4 rounded border px-4 py-3"
            style={{ borderColor: "#e0e0e0", backgroundColor: "#fafafa" }}
          >
            <p
              className="mb-2 text-2xs font-semibold"
              style={{ color: "#333333" }}
            >
              本次分析耗时
            </p>
            <div className="flex flex-col gap-1.5 text-2xs">
              <div className="flex items-center justify-between">
                <span style={{ color: "#616161" }}>知识库召回</span>
                <span
                  className="font-mono font-semibold"
                  style={{ color: "#0e639c" }}
                >
                  {fmt(t.retrieval_ms)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span style={{ color: "#616161" }}>重排</span>
                <span
                  className="font-mono font-semibold"
                  style={{ color: "#0e639c" }}
                >
                  {fmt(t.rerank_ms)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span style={{ color: "#616161" }}>调用模型分析</span>
                <span
                  className="font-mono font-semibold"
                  style={{ color: "#0e639c" }}
                >
                  {fmt(t.model_ms)}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* 确定按钮 */}
        <div
          className="flex justify-end border-t px-5 py-3"
          style={{ borderColor: "#e0e0e0" }}
        >
          <button
            type="button"
            onClick={dismiss}
            className="rounded px-5 py-1.5 text-ui font-medium text-white"
            style={{ backgroundColor: "#0e639c", cursor: "pointer" }}
          >
            确定
          </button>
        </div>
      </div>
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
  const loading = useDocReviewStore((s) => s.loading);
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
    // 读取已持久化结果中（切换文档）：轻量加载态，不是「正在分析」遮罩
    if (loading) {
      return (
        <div
          className="flex h-full flex-col items-center justify-center gap-2 text-2xs"
          style={{ color: "#616161" }}
        >
          <span
            className="animate-spin-ring rounded-full"
            style={{
              width: 22,
              height: 22,
              border: "3px solid #0e639c33",
              borderTopColor: "#0e639c",
            }}
          />
          正在加载审核结果…
        </div>
      );
    }
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
        <AnalysisDoneDialog />
        <RiskBar findings={findings} />
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
                  id={`block-${block.block_id}`}
                  className="mb-3 whitespace-pre-line"
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
