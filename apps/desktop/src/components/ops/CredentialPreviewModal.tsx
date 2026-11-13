/**
 * CredentialPreviewModal —— 凭证预览（2026-11-09）。
 *
 * 回答客户经理最常问的三件事：
 *   1. 这笔业务按专家团预设要传哪几张凭证，实传了哪几张、缺哪张；
 *   2. 每张凭证采到了哪些要素，值是 OCR 填的还是手敲的；
 *   3. 点某个已录入的要素名 → 影像放大并高亮它对应的那块区域。
 *
 * 上传/归类/拖动写操作只在办理界面（readOnly=false）开放；
 * 业务汇总页复用同一个弹窗回看历史业务，只读不写。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { collectNodes, useCredentialStore } from "@/store/credentialStore";
import {
  applyWheelZoom,
  fitScale,
  focusTransform,
  type ViewTransform,
} from "@/lib/ocrLocate";
import type {
  CredentialAsset,
  CredentialFieldValue,
  CredentialMemberView,
  CredentialNodeView,
  CredentialTeamView,
} from "@/types/ops";
import {
  CloseIcon,
  ReceiptIcon,
  RefreshIcon,
  WarningIcon,
} from "@/components/ui/Icons";

/** 要素来源徽标：一眼区分「图上能定位」与「人工录入」 */
const SOURCE_META: Record<
  CredentialFieldValue["source"],
  { label: string; color: string; bg: string }
> = {
  ai: { label: "AI 提取", color: "#0451a5", bg: "#eaf2fb" },
  ocr: { label: "OCR 填入", color: "#059669", bg: "#e9f7f1" },
  manual: { label: "手动录入", color: "#6b7280", bg: "#f3f4f6" },
};

const CLASSIFIED_LABEL: Record<string, string> = {
  keyword: "关键词自动挂载",
  ai: "AI 判定挂载",
  manual: "人工拖动挂载",
};

const ASSET_STATUS_DOT: Record<string, { color: string; label: string }> = {
  passed: { color: "#10a37f", label: "审核通过" },
  rejected: { color: "#cd3131", label: "被打回" },
  reviewing: { color: "#0451a5", label: "审核中" },
  pending: { color: "#b25c1a", label: "待审核" },
  "": { color: "#9ca3af", label: "识别底图" },
};

function fmtTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number): string => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function CredentialPreviewModal({
  caseId,
  teamIds,
  readOnly = false,
  initialNodeKey = "",
  onClose,
}: {
  caseId: string;
  teamIds: string[];
  /** 业务汇总页回看：不给上传/归类/拖动/删除入口 */
  readOnly?: boolean;
  /** 从材料行「凭证定位」进入时直接选中该资产所在节点 */
  initialNodeKey?: string;
  onClose: () => void;
}): JSX.Element {
  const view = useCredentialStore((s) => s.view);
  const loading = useCredentialStore((s) => s.loading);
  const classifying = useCredentialStore((s) => s.classifying);
  const error = useCredentialStore((s) => s.error);
  const selectedNodeKey = useCredentialStore((s) => s.selectedNodeKey);
  const selectedAssetId = useCredentialStore((s) => s.selectedAssetId);
  const asset = useCredentialStore((s) => s.asset);
  const assetLoading = useCredentialStore((s) => s.assetLoading);
  const highlight = useCredentialStore((s) => s.highlight);
  const locateNote = useCredentialStore((s) => s.locateNote);
  const busyAssetId = useCredentialStore((s) => s.busyAssetId);
  const load = useCredentialStore((s) => s.load);
  const selectNode = useCredentialStore((s) => s.selectNode);
  const selectAsset = useCredentialStore((s) => s.selectAsset);
  const classify = useCredentialStore((s) => s.classify);
  const assign = useCredentialStore((s) => s.assign);
  const recognize = useCredentialStore((s) => s.recognize);
  const locate = useCredentialStore((s) => s.locate);
  const [dragAssetId, setDragAssetId] = useState("");
  const [dropTargetKey, setDropTargetKey] = useState("");
  const [transform, setTransform] = useState<ViewTransform>({
    scale: 1,
    tx: 0,
    ty: 0,
  });
  const [imgSize, setImgSize] = useState({ w: 0, h: 0 });
  const [panning, setPanning] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const panOrigin = useRef<{
    x: number;
    y: number;
    tx: number;
    ty: number;
  } | null>(null);

  // teamIds 由调用方传字面量数组也能稳定重载（只按拼接后的 key 走依赖）
  const teamKey = teamIds.join(",");
  const teamIdsRef = useRef(teamIds);
  teamIdsRef.current = teamIds;
  useEffect(() => {
    void load(caseId, teamIdsRef.current);
  }, [caseId, teamKey, load]);

  // 外部指定节点（材料行「凭证定位」进入）：聚合到位后选一次
  const appliedInitial = useRef("");
  useEffect(() => {
    if (
      !initialNodeKey ||
      appliedInitial.current === `${caseId}#${initialNodeKey}`
    )
      return;
    if (!view) return;
    appliedInitial.current = `${caseId}#${initialNodeKey}`;
    void selectNode(initialNodeKey);
  }, [initialNodeKey, view, caseId, selectNode]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const nodes = useMemo(() => collectNodes(view), [view]);
  const stats = view?.stats;

  /** 团 / 专家的必传齐备度（左栏分组徽章用，口径与顶部 stats 一致） */
  const teamRequired = (team: CredentialTeamView): { ok: number; total: number } => {
    let ok = 0;
    let total = 0;
    for (const member of team.members) {
      for (const node of member.credentials) {
        if (!node.required) continue;
        total += 1;
        if (node.satisfied) ok += 1;
      }
    }
    return { ok, total };
  };
  const memberGap = (member: CredentialMemberView): number =>
    member.credentials.filter((n) => n.required && !n.satisfied).length;

  /** 顶部缺口 chip：直接跳到第一张缺失凭证（找不到重点时给一条最短路径） */
  const focusFirstMissing = (): void => {
    const first = view?.stats?.missing?.[0];
    if (!first) return;
    const node = nodes.find(
      (n) => n.memberKey === first.member_key && n.name === first.credential,
    );
    if (node) void selectNode(node.key);
  };

  /** 视口尺寸（jsdom 里全 0，用固定值兜底保证定位变换可测） */
  const viewportSize = useCallback((): { w: number; h: number } => {
    const el = viewportRef.current;
    const w = el ? el.clientWidth : 0;
    const h = el ? el.clientHeight : 0;
    return { w: w > 0 ? w : 720, h: h > 0 ? h : 520 };
  }, []);

  const naturalSize = useCallback((): { w: number; h: number } => {
    if (imgSize.w > 0 && imgSize.h > 0) return imgSize;
    if (asset && asset.width > 0 && asset.height > 0) {
      return { w: asset.width, h: asset.height };
    }
    return { w: 0, h: 0 };
  }, [imgSize, asset]);

  const applyFit = useCallback((): void => {
    const { w, h } = naturalSize();
    const view0 = viewportSize();
    if (w <= 0 || h <= 0) {
      setTransform({ scale: 1, tx: 0, ty: 0 });
      return;
    }
    const scale = fitScale(w, h, view0.w, view0.h);
    setTransform({
      scale,
      tx: (view0.w - w * scale) / 2,
      ty: (view0.h - h * scale) / 2,
    });
  }, [naturalSize, viewportSize]);

  // 影像换了（或首次拿到尺寸）→ 先适应窗口
  useEffect(() => {
    if (asset?.dataUrl) applyFit();
  }, [asset?.assetId, asset?.dataUrl, imgSize.w, imgSize.h, applyFit]);

  // 点了要素名 → 把命中区域居中放大（同一个字段连点也重新居中一次）
  useEffect(() => {
    if (!highlight || !asset || highlight.assetId !== asset.assetId) return;
    const { w: iw, h: ih } = naturalSize();
    const v = viewportSize();
    if (iw <= 0 || ih <= 0) return;
    setTransform(focusTransform(highlight.rect, iw, ih, v.w, v.h));
    // seq 参与依赖：连点同一字段要能再次居中
  }, [
    highlight?.seq,
    highlight?.assetId,
    asset?.assetId,
    naturalSize,
    viewportSize,
  ]);

  const onWheel = (e: React.WheelEvent<HTMLDivElement>): void => {
    const el = viewportRef.current;
    if (!el) return;
    const box = el.getBoundingClientRect();
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    setTransform((t) =>
      applyWheelZoom(t, factor, e.clientX - box.left, e.clientY - box.top),
    );
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (!asset?.dataUrl) return;
    panOrigin.current = {
      x: e.clientX,
      y: e.clientY,
      tx: transform.tx,
      ty: transform.ty,
    };
    setPanning(true);
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>): void => {
    const start = panOrigin.current;
    if (!panning || !start) return;
    setTransform((t) => ({
      ...t,
      tx: start.tx + (e.clientX - start.x),
      ty: start.ty + (e.clientY - start.y),
    }));
  };

  const endPan = (): void => {
    panOrigin.current = null;
    setPanning(false);
  };

  const zoomBy = (factor: number): void => {
    const v = viewportSize();
    setTransform((t) => applyWheelZoom(t, factor, v.w / 2, v.h / 2));
  };

  const handleDrop = (
    e: React.DragEvent,
    node: { memberKey: string; name: string; key: string },
  ): void => {
    e.preventDefault();
    setDropTargetKey("");
    const assetId = e.dataTransfer.getData("text/plain") || dragAssetId;
    setDragAssetId("");
    if (!assetId || readOnly) return;
    const found = findAsset(view, assetId);
    if (!found) return;
    if (found.member_key !== node.memberKey) {
      // 换专家 = 换审核人，不在凭证面板里悄悄改
      window.alert(
        `这份材料属于专家「${found.member_key}」，不能挂到「${node.memberKey}」的凭证上。\n` +
          `要换专家请回到办理界面，在该专家卡片下重新上传。`,
      );
      return;
    }
    void assign(assetId, node.name);
  };

  const handleDropUnassigned = (
    e: React.DragEvent,
    memberKey: string,
  ): void => {
    e.preventDefault();
    setDropTargetKey("");
    const assetId = e.dataTransfer.getData("text/plain") || dragAssetId;
    setDragAssetId("");
    if (!assetId || readOnly) return;
    const found = findAsset(view, assetId);
    if (!found || found.member_key !== memberKey) return;
    void assign(assetId, "");
  };

  const selectedNode = nodes.find((n) => n.key === selectedNodeKey) ?? null;
  const nodeView = selectedNode
    ? findNodeView(
        view,
        selectedNode.teamId,
        selectedNode.memberKey,
        selectedNode.name,
      )
    : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.45)" }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="凭证预览"
    >
      <div
        className="flex flex-col rounded-lg shadow-2xl"
        style={{
          backgroundColor: "#ffffff",
          width: "min(1180px, 94vw)",
          height: "min(88vh, 860px)",
          borderTop: "3px solid #0451a5",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部：第一行标题/操作，第二行齐备度 —— 缺什么放最显眼的位置 */}
        <div
          className="flex flex-shrink-0 flex-col gap-1.5 border-b px-4 py-2.5"
          style={{ borderColor: "#e0e0e0" }}
        >
          <div className="flex items-center gap-2">
            <span style={{ color: "#0451a5" }} aria-hidden="true">
              <ReceiptIcon size={18} />
            </span>
            <div className="min-w-0">
              <div className="text-ui font-semibold" style={{ color: "#1f1f1f" }}>
                凭证预览
              </div>
              <div className="text-[10px]" style={{ color: "#9ca3af" }}>
                {caseId}
              </div>
            </div>
            <div className="ml-auto flex items-center gap-1.5">
              {!readOnly && (
                <button
                  type="button"
                  onClick={() => void classify({ force: false })}
                  disabled={classifying || !stats?.has_preset}
                  className="btn btn-secondary btn-sm"
                  title="按文件名与识别原文做一次分类，自动挂到对应凭证下"
                >
                  {classifying ? "⏳ 归类中…" : "🪄 智能归类"}
                </button>
              )}
              {!readOnly && stats?.has_preset && (
                <button
                  type="button"
                  onClick={() => void classify({ force: true })}
                  disabled={classifying}
                  className="btn btn-ghost btn-sm"
                  title="推翻已有归属重新分类（会覆盖人工拖动以外的挂载）"
                >
                  <RefreshIcon size={12} />
                  重新归类
                </button>
              )}
              <button
                type="button"
                onClick={onClose}
                className="btn btn-ghost btn-icon"
                aria-label="关闭凭证预览"
              >
                <CloseIcon size={13} />
              </button>
            </div>
          </div>

          {/* 齐备度：进度条 + 「缺哪几张」（可点直接定位）+ 未归属 */}
          {stats && (stats.required_total > 0 || stats.unassigned_total > 0) && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {stats.required_total > 0 && (
                <div
                  className="flex items-center gap-1.5"
                  title={`必传凭证齐备度 ${stats.required_ok}/${stats.required_total}`}
                >
                  <div
                    className="h-1.5 w-24 overflow-hidden rounded-full"
                    style={{ backgroundColor: "#e7e5e4" }}
                  >
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${Math.round(
                          (stats.required_ok / stats.required_total) * 100,
                        )}%`,
                        backgroundColor:
                          stats.required_ok === stats.required_total
                            ? "#10a37f"
                            : "#b25c1a",
                      }}
                    />
                  </div>
                  <span
                    className="text-[10px] font-semibold"
                    style={{
                      color:
                        stats.required_ok === stats.required_total
                          ? "#10a37f"
                          : "#b25c1a",
                    }}
                  >
                    必传凭证 {stats.required_ok}/{stats.required_total} 已齐
                  </span>
                </div>
              )}

              {stats.required_total > 0 &&
                (stats.missing.length > 0 ? (
                  <button
                    type="button"
                    onClick={focusFirstMissing}
                    className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold transition-colors hover:brightness-95"
                    style={{
                      borderColor: "#f3c8c8",
                      backgroundColor: "#fdf0f0",
                      color: "#cd3131",
                    }}
                    title="点一下直接定位到第一张缺失的凭证"
                  >
                    <span>
                      缺 {stats.missing.length} 张：
                      {stats.missing.map((m) => m.credential).join("、")}
                    </span>
                    <span style={{ color: "#0451a5" }}>定位 →</span>
                  </button>
                ) : (
                  <span
                    className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                    style={{ backgroundColor: "#e9f7f1", color: "#10a37f" }}
                  >
                    ✓ 必传凭证已齐
                  </span>
                ))}

              {stats.unassigned_total > 0 && (
                <span
                  className="rounded-full px-2 py-0.5 text-[10px]"
                  style={{ backgroundColor: "#f3f4f6", color: "#6b7280" }}
                  title="这些材料还没挂到任何凭证节点，拖到左侧凭证上即可"
                >
                  未归属 {stats.unassigned_total}（拖到左侧凭证可归类）
                </span>
              )}
            </div>
          )}
        </div>

        {stats && !stats.has_preset && (
          <div
            className="flex-shrink-0 border-b px-4 py-1.5 text-[10px]"
            style={{
              borderColor: "#f0efed",
              backgroundColor: "#fdfaf5",
              color: "#b25c1a",
            }}
          >
            当前专家团还没有预设凭证清单 ——
            这里仍能看到已上传的全部材料，但判断不了「缺哪一张」。 请到 设置 →
            专家团 → 各专家的「凭证清单」补充。
          </div>
        )}

        {error && (
          <div
            className="flex-shrink-0 border-b px-4 py-1.5 text-[10px]"
            style={{
              borderColor: "#f3c8c8",
              backgroundColor: "#fdf3f3",
              color: "#b91c1c",
            }}
          >
            {error}
          </div>
        )}

        {/* 主体：左凭证树 / 右影像工作区 */}
        <div className="flex min-h-0 flex-1">
          <div
            className="w-[300px] flex-shrink-0 overflow-y-auto border-r p-2"
            style={{ borderColor: "#f0f0f0", backgroundColor: "#fbfbfa" }}
          >
            {loading && !view && (
              <div className="p-3 text-2xs" style={{ color: "#9ca3af" }}>
                正在读取凭证清单…
              </div>
            )}
            {view?.teams.map((team) => (
              <div key={team.team_id || team.team_name} className="mb-2">
                <div
                  className="mb-1 flex items-center justify-between gap-1 border-b px-1 pb-1 text-[10px] font-semibold uppercase tracking-wide"
                  style={{ color: "#0b6bcb", borderColor: "#e7e5e4" }}
                >
                  <span className="min-w-0 truncate">{team.team_name}</span>
                  {(() => {
                    const t = teamRequired(team);
                    if (t.total === 0) return null;
                    const done = t.ok === t.total;
                    return (
                      <span
                        className="flex-shrink-0 rounded-full px-1.5 text-[9px] font-semibold normal-case"
                        style={{
                          backgroundColor: done ? "#e9f7f1" : "#fdf0f0",
                          color: done ? "#10a37f" : "#cd3131",
                        }}
                        title={`本团必传凭证 ${t.ok}/${t.total} 已齐`}
                      >
                        {t.ok}/{t.total}
                      </span>
                    );
                  })()}
                </div>
                {team.members.map((member) => (
                  <div key={member.member_key} className="mb-1.5">
                    <div
                      className="flex items-center gap-1 px-1 py-0.5 text-[10px] font-semibold"
                      style={{ color: "#4b5563" }}
                    >
                      <span aria-hidden="true">👤</span>
                      <span className="min-w-0 truncate">{member.member_key}</span>
                      {!member.has_preset && (
                        <span style={{ color: "#9ca3af" }}>（未预设凭证）</span>
                      )}
                      {memberGap(member) > 0 && (
                        <span
                          className="ml-auto flex-shrink-0 rounded-full px-1 text-[9px] font-semibold"
                          style={{ backgroundColor: "#fdf0f0", color: "#cd3131" }}
                          title={`该专家还有 ${memberGap(member)} 张必传凭证未齐`}
                        >
                          缺 {memberGap(member)}
                        </span>
                      )}
                    </div>
                    {member.credentials.map((node) => {
                      const key = `${team.team_id}::${member.member_key}::${node.name}`;
                      const active = key === selectedNodeKey;
                      const isTarget = dropTargetKey === key;
                      // 缺图的必传凭证 = 左栏应该一眼看到的那几张
                      const isGap = node.required && !node.satisfied;
                      const missingFields = node.fields_missing.length;
                      return (
                        <div
                          key={key}
                          role="button"
                          tabIndex={0}
                          onClick={() => void selectNode(key)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void selectNode(key);
                          }}
                          onDragOver={(e) => {
                            if (readOnly) return;
                            e.preventDefault();
                            setDropTargetKey(key);
                          }}
                          onDragLeave={() =>
                            setDropTargetKey((v) => (v === key ? "" : v))
                          }
                          onDrop={(e) =>
                            handleDrop(e, {
                              memberKey: member.member_key,
                              name: node.name,
                              key,
                            })
                          }
                          className="mb-1 cursor-pointer rounded border py-1 pl-2 pr-1.5 transition-colors"
                          style={{
                            borderColor: active
                              ? "#0451a5"
                              : isTarget
                                ? "#10a37f"
                                : isGap
                                  ? "#f0c8c8"
                                  : "#ececeb",
                            backgroundColor: active
                              ? "#eaf2fb"
                              : isTarget
                                ? "#eef9f4"
                                : isGap
                                  ? "#fffdfd"
                                  : "#fbfbfa",
                            // 左侧状态条：选中蓝 / 缺图红 / 已齐无
                            boxShadow: active
                              ? "inset 3px 0 0 #0451a5"
                              : isGap
                                ? "inset 3px 0 0 #cd3131"
                                : "none",
                          }}
                          title={node.hint || undefined}
                        >
                          <div className="flex items-center gap-1.5">
                            <span
                              className="flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white"
                              style={{
                                backgroundColor: node.satisfied
                                  ? "#10a37f"
                                  : node.required
                                    ? "#cd3131"
                                    : "#c4c4c4",
                              }}
                              aria-hidden="true"
                            >
                              {node.satisfied ? "✓" : node.required ? "!" : "·"}
                            </span>
                            <span
                              className="min-w-0 flex-1 truncate text-[11px]"
                              style={{
                                color:
                                  node.satisfied && !active
                                    ? "#4b5563"
                                    : "#1f1f1f",
                                fontWeight: active ? 600 : 400,
                              }}
                            >
                              {node.name}
                            </span>
                            {node.required ? (
                              <span
                                className="flex-shrink-0 rounded px-1 text-[9px] font-semibold"
                                style={{
                                  backgroundColor: "#fdf0f0",
                                  color: "#cd3131",
                                }}
                              >
                                必传
                              </span>
                            ) : (
                              <span
                                className="flex-shrink-0 text-[9px]"
                                style={{ color: "#b0b0b0" }}
                              >
                                选传
                              </span>
                            )}
                          </div>
                          <div
                            className="mt-0.5 flex items-center gap-1 pl-[18px] text-[9px]"
                            style={{ color: "#9ca3af" }}
                          >
                            <span
                              style={{
                                color: isGap ? "#cd3131" : "#9ca3af",
                                fontWeight: isGap ? 600 : 400,
                              }}
                            >
                              {node.assets.length > 0
                                ? `${node.assets.length} 份`
                                : isGap
                                  ? "未上传"
                                  : "选传未传"}
                            </span>
                            {node.field_values.length > 0 && (
                              <span>· 已采 {node.field_values.length} 项</span>
                            )}
                            {missingFields > 0 && (
                              <span style={{ color: "#b25c1a" }}>
                                · 缺 {missingFields} 项
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                    {member.unassigned.length > 0 && (
                      <div
                        className="mt-1 rounded border border-dashed p-1.5"
                        style={{
                          borderColor:
                            dropTargetKey === `un::${member.member_key}`
                              ? "#0451a5"
                              : "#d4d4d4",
                          backgroundColor: "#ffffff",
                        }}
                        onDragOver={(e) => {
                          if (readOnly) return;
                          e.preventDefault();
                          setDropTargetKey(`un::${member.member_key}`);
                        }}
                        onDragLeave={() =>
                          setDropTargetKey((v) =>
                            v === `un::${member.member_key}` ? "" : v,
                          )
                        }
                        onDrop={(e) =>
                          handleDropUnassigned(e, member.member_key)
                        }
                      >
                        <div
                          className="mb-1 text-[9px] font-semibold"
                          style={{ color: "#9ca3af" }}
                        >
                          未归属（{member.unassigned.length}）·
                          拖到上面的凭证即可挂载
                        </div>
                        {member.unassigned.map((a) => (
                          <AssetChip
                            key={a.id}
                            asset={a}
                            selected={a.id === selectedAssetId}
                            readOnly={readOnly}
                            onSelect={(id) => void selectAsset(id)}
                            onDragStart={(id) => setDragAssetId(id)}
                            onDragEnd={() => {
                              setDragAssetId("");
                              setDropTargetKey("");
                            }}
                            dragging={dragAssetId === a.id}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>

          {/* 右：影像工作区 + 要素清单 */}
          <div className="flex min-w-0 flex-1 flex-col">
            {!selectedNode || !nodeView ? (
              <div
                className="flex flex-1 items-center justify-center p-6 text-2xs"
                style={{ color: "#9ca3af" }}
              >
                {view
                  ? "这个业务还没有预设凭证 —— 左侧可直接查看已上传材料"
                  : "选择左侧凭证查看影像与要素"}
              </div>
            ) : (
              <>
                <div
                  className="flex flex-shrink-0 flex-col gap-1 border-b px-3 py-2"
                  style={{ borderColor: "#f0f0f0", backgroundColor: "#fafaf9" }}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className="text-ui-lg font-semibold"
                      style={{ color: "#1f1f1f" }}
                    >
                      {nodeView.name}
                    </span>
                    {/* 状态徐章：这页最该先看的东西 */}
                    <span
                      className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                      style={{
                        backgroundColor: nodeView.satisfied
                          ? "#e9f7f1"
                          : nodeView.required
                            ? "#fdf0f0"
                            : "#f3f4f6",
                        color: nodeView.satisfied
                          ? "#10a37f"
                          : nodeView.required
                            ? "#cd3131"
                            : "#6b7280",
                      }}
                    >
                      {nodeView.satisfied
                        ? "✓ 已上传"
                        : nodeView.required
                          ? "✗ 还没上传（必传）"
                          : "○ 还没上传（选传）"}
                    </span>
                    {nodeView.assets.length > 0 && (
                      <span className="text-[10px]" style={{ color: "#9ca3af" }}>
                        {nodeView.assets.length} 份材料
                      </span>
                    )}
                  </div>
                  <div
                    className="flex flex-wrap items-center gap-1.5 text-[10px]"
                    style={{ color: "#9ca3af" }}
                  >
                    <span>
                      {nodeView.media === "image"
                        ? "影像凭证"
                        : nodeView.media === "doc"
                          ? "文档凭证"
                          : "不限介质"}
                    </span>
                    {nodeView.hint && (
                      <>
                        <span>·</span>
                        <span className="min-w-0 truncate" title={nodeView.hint}>
                          要求：{nodeView.hint}
                        </span>
                      </>
                    )}
                  </div>
                </div>

                {/* 该节点下的资产条（多份可切换，缩略图 chip 可拖动改归属） */}
                {nodeView.assets.length > 0 && (
                  <div
                    className="flex flex-shrink-0 gap-1.5 overflow-x-auto border-b px-3 py-1.5"
                    style={{ borderColor: "#f0f0f0" }}
                  >
                    {nodeView.assets.map((a) => (
                      <AssetChip
                        key={a.id}
                        asset={a}
                        selected={a.id === selectedAssetId}
                        readOnly={readOnly}
                        onSelect={(id) => void selectAsset(id)}
                        onDragStart={(id) => setDragAssetId(id)}
                        onDragEnd={() => {
                          setDragAssetId("");
                          setDropTargetKey("");
                        }}
                        dragging={dragAssetId === a.id}
                      />
                    ))}
                  </div>
                )}

                {/* 影像区 */}
                <div className="flex min-h-0 flex-1 flex-col">
                  {nodeView.assets.length === 0 ? (
                    <div className="flex flex-1 flex-col items-center justify-center p-6">
                      <div
                        className="flex max-w-md flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-7 text-center"
                        style={{
                          borderColor: "#e7e5e4",
                          backgroundColor: "#fafaf9",
                        }}
                      >
                        <span
                          className="flex h-11 w-11 items-center justify-center rounded-full text-[18px] font-bold"
                          style={{
                            backgroundColor: nodeView.required
                              ? "#fdf0f0"
                              : "#f3f4f6",
                            color: nodeView.required ? "#cd3131" : "#9ca3af",
                          }}
                          aria-hidden="true"
                        >
                          ⬆
                        </span>
                        <span
                          className="text-ui-lg font-semibold"
                          style={{ color: "#1f1f1f" }}
                        >
                          {nodeView.required
                            ? "这张凭证还没有上传"
                            : "这张凭证（选传）还没有上传"}
                        </span>
                        <span
                          className="text-[11px] leading-relaxed"
                          style={{ color: "#6b7280" }}
                        >
                          回到办理界面，在「{selectedNode.memberKey}
                          」的卡片下上传材料；
                          已上传但未归类的材料可直接拖到左侧这个凭证上。
                        </span>
                        {!readOnly && (
                          <span
                            className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                            style={{
                              backgroundColor: "#eaf2fb",
                              color: "#0451a5",
                            }}
                          >
                            左侧「未归属」的材料可直接拖到这张凭证
                          </span>
                        )}
                      </div>
                    </div>
                  ) : assetLoading ? (
                    <div
                      className="flex flex-1 items-center justify-center text-2xs"
                      style={{ color: "#9ca3af" }}
                    >
                      正在读取影像…
                    </div>
                  ) : asset?.dataUrl ? (
                    <>
                      <div
                        className="flex flex-shrink-0 items-center gap-1.5 border-b px-3 py-1"
                        style={{
                          borderColor: "#f0f0f0",
                          backgroundColor: "#ffffff",
                        }}
                      >
                        <button
                          type="button"
                          onClick={() => zoomBy(1 / 1.25)}
                          className="btn btn-ghost btn-xs"
                          title="缩小"
                        >
                          －
                        </button>
                        <span
                          className="text-[10px]"
                          style={{ color: "#6b7280" }}
                        >
                          {(transform.scale * 100).toFixed(0)}%
                        </span>
                        <button
                          type="button"
                          onClick={() => zoomBy(1.25)}
                          className="btn btn-ghost btn-xs"
                          title="放大"
                        >
                          ＋
                        </button>
                        <button
                          type="button"
                          onClick={applyFit}
                          className="btn btn-ghost btn-xs"
                          title="整图适应窗口"
                        >
                          ⤢ 适应
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            const v = viewportSize();
                            const n = naturalSize();
                            setTransform({
                              scale: 1,
                              tx: (v.w - n.w) / 2,
                              ty: (v.h - n.h) / 2,
                            });
                          }}
                          className="btn btn-ghost btn-xs"
                          title="按原始像素显示（可拖动平移）"
                        >
                          1:1
                        </button>
                        {!asset.blocks.length && (
                          <button
                            type="button"
                            onClick={() => void recognize(asset.assetId)}
                            disabled={busyAssetId === asset.assetId}
                            className="btn btn-secondary btn-xs ml-auto"
                            title="端侧 OCR 逐行识别（约 10-30 秒），识别后才能按要素定位高亮"
                          >
                            {busyAssetId === asset.assetId
                              ? "⏳ 识别中…"
                              : "🔍 立即识别影像"}
                          </button>
                        )}
                        {highlight && (
                          <span
                            className="ml-auto text-[10px]"
                            style={{ color: "#0451a5" }}
                          >
                            已定位 · 识别原文「
                            {highlight.matched[0]?.slice(0, 24)}」
                            {highlight.confidence < 0.6
                              ? " · 低置信，请核对"
                              : ""}
                          </span>
                        )}
                      </div>
                      <div
                        ref={viewportRef}
                        className="relative min-h-0 flex-1 overflow-hidden"
                        style={{
                          backgroundColor: "#f5f5f4",
                          cursor: panning ? "grabbing" : "grab",
                        }}
                        onWheel={onWheel}
                        onPointerDown={onPointerDown}
                        onPointerMove={onPointerMove}
                        onPointerUp={endPan}
                        onPointerLeave={endPan}
                      >
                        <div
                          style={{
                            position: "absolute",
                            left: 0,
                            top: 0,
                            transform: `translate(${transform.tx}px, ${transform.ty}px) scale(${transform.scale})`,
                            transformOrigin: "0 0",
                          }}
                        >
                          <img
                            src={asset.dataUrl}
                            alt={asset.fileName}
                            draggable={false}
                            onLoad={(e) => {
                              const el = e.currentTarget;
                              if (el.naturalWidth > 0) {
                                setImgSize({
                                  w: el.naturalWidth,
                                  h: el.naturalHeight,
                                });
                              }
                            }}
                            style={{ display: "block", maxWidth: "none" }}
                          />
                          {highlight && highlight.assetId === asset.assetId && (
                            <svg
                              width={imgSize.w || asset.width || 1}
                              height={imgSize.h || asset.height || 1}
                              viewBox={`0 0 ${imgSize.w || asset.width || 1} ${imgSize.h || asset.height || 1}`}
                              style={{
                                position: "absolute",
                                left: 0,
                                top: 0,
                                pointerEvents: "none",
                              }}
                              data-testid="credential-highlight-layer"
                            >
                              <rect
                                x={Math.max(0, highlight.rect.x - 4)}
                                y={Math.max(0, highlight.rect.y - 4)}
                                width={highlight.rect.w + 8}
                                height={highlight.rect.h + 8}
                                fill="rgba(16,163,127,0.14)"
                                stroke="#10a37f"
                                strokeWidth={3}
                                strokeDasharray="6 3"
                              />
                            </svg>
                          )}
                        </div>
                        {!highlight && locateNote === "" && (
                          <div
                            className="pointer-events-none absolute bottom-1.5 left-1.5 rounded px-1.5 py-0.5 text-[10px]"
                            style={{
                              backgroundColor: "rgba(255,255,255,0.85)",
                              color: "#9ca3af",
                            }}
                          >
                            滚轮缩放 · 拖动平移 · 点下方要素名定位
                          </div>
                        )}
                      </div>
                    </>
                  ) : (
                    <div className="flex flex-1 flex-col items-center justify-center gap-1 p-6 text-center">
                      <span className="text-2xs" style={{ color: "#6b7280" }}>
                        {asset
                          ? "这份凭证不是图片，没有影像可放大"
                          : "选择上方一份凭证查看影像"}
                      </span>
                      {asset && !asset.blocks.length && !readOnly && (
                        <button
                          type="button"
                          onClick={() => void recognize(asset.assetId)}
                          className="btn btn-secondary btn-xs"
                        >
                          🔍 立即识别影像
                        </button>
                      )}
                    </div>
                  )}

                  {locateNote && (
                    <div
                      className="flex flex-shrink-0 items-center gap-1 border-t px-3 py-1 text-[10px]"
                      style={{
                        borderColor: "#f0efed",
                        backgroundColor: "#fdfaf5",
                        color: "#b25c1a",
                      }}
                    >
                      <WarningIcon size={11} />
                      {locateNote}
                    </div>
                  )}

                  {/* 要素清单：点 label 定位并高亮影像区域 */}
                  <div
                    className="flex-shrink-0 border-t px-3 py-2"
                    style={{
                      borderColor: "#f0f0f0",
                      backgroundColor: "#ffffff",
                    }}
                  >
                    <div className="mb-1.5 flex items-center gap-2">
                      <span
                        className="text-[10px] font-semibold"
                        style={{ color: "#6b7280" }}
                      >
                        凭证要素
                      </span>
                      <span className="text-[9px]" style={{ color: "#9ca3af" }}>
                        点要素名 → 放大并高亮图上对应区域
                      </span>
                      {nodeView.field_values.length +
                        nodeView.fields_missing.length >
                        0 && (
                        <span
                          className="ml-auto text-[10px] font-semibold"
                          style={{
                            color:
                              nodeView.fields_missing.length > 0
                                ? "#b25c1a"
                                : "#10a37f",
                          }}
                        >
                          已采 {nodeView.field_values.length}/
                          {nodeView.field_values.length +
                            nodeView.fields_missing.length}
                        </span>
                      )}
                    </div>
                    {nodeView.field_values.length === 0 &&
                    nodeView.fields_missing.length === 0 ? (
                      <div className="text-[10px]" style={{ color: "#9ca3af" }}>
                        这张凭证没有预设要采集的要素 —— 到 设置 → 专家团
                        的凭证清单里补充
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {nodeView.field_values.map((f) => (
                          <FieldPill
                            key={`${f.name}-${f.asset_id}`}
                            field={f}
                            onLocate={() => void locate(f)}
                          />
                        ))}
                        {nodeView.fields_missing.map((m) => (
                          <span
                            key={`missing-${m.name}`}
                            className="rounded border border-dashed px-1.5 py-0.5 text-[10px]"
                            style={{
                              borderColor: "#e0e0e0",
                              backgroundColor: "#fbfbfa",
                              color: "#9ca3af",
                            }}
                            title={`「${m.name}」还没有采集到值`}
                          >
                            {m.label}：未采集
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        <div
          className="flex flex-shrink-0 items-center gap-2 border-t px-4 py-2"
          style={{ borderColor: "#e0e0e0", backgroundColor: "#fbfbfa" }}
        >
          <span
            className="min-w-0 flex-1 text-[10px]"
            style={{ color: "#9ca3af" }}
          >
            {readOnly
              ? "历史业务回看（只读）：影像与要素可查，挂载关系已固化。"
              : "凭证影像只在端侧识别（数据不出域）；未归属材料可拖到对应凭证上，改动会记入审计。"}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-primary btn-sm"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

/** 凭证资产 chip：缩略状态 + 文件名；可点选、可拖动改归属 */
function AssetChip({
  asset,
  selected,
  readOnly,
  dragging,
  onSelect,
  onDragStart,
  onDragEnd,
}: {
  asset: CredentialAsset;
  selected: boolean;
  readOnly: boolean;
  dragging: boolean;
  onSelect: (id: string) => void;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
}): JSX.Element {
  const dot = ASSET_STATUS_DOT[asset.status] ?? ASSET_STATUS_DOT[""];
  const recognized = asset.has_ocr;
  return (
    <div
      role="button"
      tabIndex={0}
      draggable={!readOnly}
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", asset.id);
        e.dataTransfer.effectAllowed = "move";
        onDragStart(asset.id);
      }}
      onDragEnd={onDragEnd}
      onClick={() => onSelect(asset.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onSelect(asset.id);
      }}
      className="mb-1 flex min-w-0 cursor-pointer items-center gap-1 rounded border px-1.5 py-1"
      style={{
        borderColor: selected ? "#0451a5" : "#e0e0e0",
        backgroundColor: selected ? "#eaf2fb" : "#fbfbfa",
        opacity: dragging ? 0.45 : 1,
        cursor: readOnly ? "default" : dragging ? "grabbing" : "grab",
      }}
      title={`${asset.file_name}｜${dot.label}${asset.classified_by ? `｜${CLASSIFIED_LABEL[asset.classified_by] ?? ""}` : ""}`}
    >
      <span
        className="flex-shrink-0 text-[10px]"
        style={{ color: dot.color }}
        aria-hidden="true"
      >
        {asset.is_image ? "📷" : "📄"}
      </span>
      <span
        className="min-w-0 flex-1 truncate text-[10px]"
        style={{ color: "#1f1f1f" }}
      >
        {asset.file_name}
      </span>
      {!recognized && asset.is_image && (
        <span
          className="flex-shrink-0 text-[9px]"
          style={{ color: "#b25c1a" }}
          title="还没有识别块，点「立即识别影像」"
        >
          未识别
        </span>
      )}
      {asset.kind === "image" && (
        <span
          className="flex-shrink-0 rounded px-1 text-[9px]"
          style={{ backgroundColor: "#e9f7f1", color: "#059669" }}
        >
          底图
        </span>
      )}
      <span className="flex-shrink-0 text-[9px]" style={{ color: "#9ca3af" }}>
        {fmtTime(asset.created_at)}
      </span>
    </div>
  );
}

/** 要素药丸：来源徽标 + 值；点 label 触发定位 */
function FieldPill({
  field,
  onLocate,
}: {
  field: CredentialFieldValue;
  onLocate: () => void;
}): JSX.Element {
  const meta = SOURCE_META[field.source] ?? SOURCE_META.manual;
  const low =
    field.source === "ai" && field.confidence > 0 && field.confidence < 0.6;
  return (
    <span
      className="flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px]"
      style={{
        borderColor: low ? "#e8b4b4" : "#e2e8e6",
        backgroundColor: low ? "#fdf5f5" : "#f7fbf9",
        color: "#374151",
      }}
      title={
        field.can_locate
          ? `点「${field.label}」在影像上定位高亮${low ? `（置信度 ${(field.confidence * 100).toFixed(0)}%，请核对）` : ""}`
          : `「${field.label}」来自${field.source === "manual" ? "手动录入" : "无影像块的来源"}，无法定位`
      }
    >
      <button
        type="button"
        onClick={onLocate}
        className="font-semibold underline decoration-dotted underline-offset-2"
        style={{
          color: field.can_locate ? "#0451a5" : "#9ca3af",
          textDecoration: field.can_locate ? undefined : "none",
        }}
      >
        {field.label}
      </button>
      <span style={{ color: "#9ca3af" }}>：</span>
      <span
        className="max-w-[180px] truncate"
        style={{ color: low ? "#b91c1c" : "#1f1f1f" }}
      >
        {low && "⚠ "}
        {field.value}
      </span>
      <span
        className="flex-shrink-0 rounded px-1 text-[9px]"
        style={{ backgroundColor: meta.bg, color: meta.color }}
      >
        {meta.label}
      </span>
    </span>
  );
}

function findAsset(
  view: ReturnType<typeof useCredentialStore.getState>["view"],
  id: string,
): CredentialAsset | null {
  for (const team of view?.teams ?? []) {
    for (const member of team.members) {
      const hit =
        member.unassigned.find((a) => a.id === id) ??
        member.credentials.flatMap((n) => n.assets).find((a) => a.id === id);
      if (hit) return hit;
    }
  }
  return null;
}

function findNodeView(
  view: ReturnType<typeof useCredentialStore.getState>["view"],
  teamId: string,
  memberKey: string,
  name: string,
): CredentialNodeView | null {
  for (const team of view?.teams ?? []) {
    if (team.team_id !== teamId) continue;
    for (const member of team.members) {
      if (member.member_key !== memberKey) continue;
      const node = member.credentials.find((n) => n.name === name);
      if (node) return node;
    }
  }
  return null;
}
