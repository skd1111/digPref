/**
 * BusinessSummaryView —— 业务汇总（业务办理台账查询页，2026-11-09）。
 *
 * 入口：左侧 ActivityBar「业务汇总」图标 → 主区全屏（本页横切所有模式，
 * 包括运营模式；运营模式里点它也能查业务办理情况）。
 *
 * 台账口径：业务**发起即建档**（客户经理上传第一份材料时），状态四态 ——
 *   🟦 正在办理 / 🟨 挂起 / 🟧 人工确认 / ⬜ 结束。
 * 支持多维查询：业务大类、小类、状态、发起日期区间、关键词。
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import { OPS_MODULES } from "@/components/ops/opsModules";
import { CredentialPreviewModal } from "@/components/ops/CredentialPreviewModal";
import { useOpsTaskStore } from "@/store/opsTaskStore";
import {
  OPS_TASK_STATUS_META,
  OPS_TASK_STATUSES,
  type OpsBusinessTask,
  type OpsTaskStatus,
} from "@/types/ops";
import { RefreshIcon, TableIcon } from "@/components/ui/Icons";

function fmtTime(ts: number): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n: number): string => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** <input type="date"> 值（YYYY-MM-DD）→ 当日 00:00:00 的 epoch 秒 */
function dateToEpoch(value: string, endOfDay: boolean): number | undefined {
  if (!value) return undefined;
  const ts = new Date(`${value}T00:00:00`).getTime() / 1000;
  if (Number.isNaN(ts)) return undefined;
  return endOfDay ? Math.floor(ts) + 86399 : Math.floor(ts);
}

/** epoch 秒 → <input type="date"> 值 */
function epochToDate(ts: number | undefined): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number): string => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 汇总页回看不预设团范围：后端按台账已存材料自行聚合，固定空数组避免重复拉 */
const NO_TEAM_IDS: string[] = [];

export function BusinessSummaryView(): JSX.Element {
  const tasks = useOpsTaskStore((s) => s.tasks);
  const counts = useOpsTaskStore((s) => s.counts);
  const loading = useOpsTaskStore((s) => s.loading);
  const error = useOpsTaskStore((s) => s.error);
  const query = useOpsTaskStore((s) => s.query);
  const detail = useOpsTaskStore((s) => s.detail);
  const loadTasks = useOpsTaskStore((s) => s.loadTasks);
  const loadDetail = useOpsTaskStore((s) => s.loadDetail);
  const setQuery = useOpsTaskStore((s) => s.setQuery);
  const resetQuery = useOpsTaskStore((s) => s.resetQuery);

  const [keyword, setKeyword] = useState(query.keyword ?? "");
  const [dateFrom, setDateFrom] = useState(epochToDate(query.date_from));
  const [dateTo, setDateTo] = useState(epochToDate(query.date_to));
  const [expandedId, setExpandedId] = useState("");
  /** 凭证预览弹窗（已结束 / 挂起的业务只读回看） */
  const [credentialView, setCredentialView] = useState<{
    caseId: string;
    readOnly: boolean;
  } | null>(null);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  // 业务大类 → 小类（选大类时清掉不属于该大类的小类）
  const subItems = useMemo(() => {
    const mod = OPS_MODULES.find((m) => m.id === query.category);
    return mod ? mod.items : [];
  }, [query.category]);

  const toggleStatus = (s: OpsTaskStatus): void => {
    const cur = query.status ?? [];
    const next = cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s];
    setQuery({ status: next.length > 0 ? next : undefined });
  };

  const applyFilters = (): void => {
    setQuery({
      keyword: keyword.trim() || undefined,
      date_from: dateToEpoch(dateFrom, false),
      date_to: dateToEpoch(dateTo, true),
    });
  };

  const onToggleRow = (t: OpsBusinessTask): void => {
    if (expandedId === t.id) {
      setExpandedId("");
      return;
    }
    setExpandedId(t.id);
    void loadDetail(t.id);
  };

  return (
    <div
      className="flex h-full flex-col"
      style={{ backgroundColor: "#ffffff" }}
    >
      {/* 页头 */}
      <div
        className="flex flex-shrink-0 items-center gap-2 border-b px-4 py-2"
        style={{ borderColor: "#e0e0e0" }}
      >
        <TableIcon size={16} />
        <span className="text-ui font-semibold" style={{ color: "#1f1f1f" }}>
          业务汇总
        </span>
        <span className="text-2xs" style={{ color: "#9ca3af" }}>
          业务发起即登记 · 正在办理 / 挂起 / 人工确认 / 结束 —— 共{" "}
          {tasks.length} 条
        </span>
        <button
          type="button"
          onClick={() => void loadTasks()}
          className="btn btn-sm ml-auto"
          title="重新拉取台账"
        >
          <RefreshIcon size={12} />
          刷新
        </button>
      </div>

      {/* 状态计数条：点一下即按该状态筛选 */}
      <div
        className="flex flex-shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2"
        style={{ borderColor: "#f0f0f0", backgroundColor: "#fbfbfa" }}
      >
        {OPS_TASK_STATUSES.map((s) => {
          const meta = OPS_TASK_STATUS_META[s];
          const active = (query.status ?? []).includes(s);
          return (
            <button
              key={s}
              type="button"
              onClick={() => toggleStatus(s)}
              className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-2xs font-semibold"
              style={{
                borderColor: active ? meta.color : "#e0e0e0",
                backgroundColor: active ? `${meta.color}18` : "#ffffff",
                color: active ? meta.color : "#616161",
              }}
              title={`按「${meta.label}」筛选（可多选）`}
            >
              <span aria-hidden="true">{meta.icon}</span>
              {meta.label}
              <span style={{ color: active ? meta.color : "#9ca3af" }}>
                {counts[s] ?? 0}
              </span>
            </button>
          );
        })}
        {(query.status ?? []).length > 0 && (
          <button
            type="button"
            onClick={() => setQuery({ status: undefined })}
            className="text-2xs"
            style={{ color: "#0451a5" }}
          >
            清除状态筛选
          </button>
        )}
      </div>

      {/* 筛选行：业务大类 / 小类 / 日期区间 / 关键词 */}
      <div
        className="flex flex-shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2"
        style={{ borderColor: "#f0f0f0" }}
      >
        <label
          className="flex items-center gap-1 text-2xs"
          style={{ color: "#6b7280" }}
        >
          业务大类
          <select
            value={query.category ?? ""}
            onChange={(e) =>
              setQuery({
                category: e.target.value || undefined,
                feature_id: undefined,
              })
            }
            className="rounded border px-1.5 py-0.5 text-2xs outline-none focus:border-[#10a37f]"
            style={{
              borderColor: "#d4d4d4",
              backgroundColor: "#ffffff",
              color: "#202124",
            }}
          >
            <option value="">全部</option>
            {OPS_MODULES.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label
          className="flex items-center gap-1 text-2xs"
          style={{ color: "#6b7280" }}
        >
          小类
          <select
            value={query.feature_id ?? ""}
            onChange={(e) =>
              setQuery({ feature_id: e.target.value || undefined })
            }
            disabled={!query.category}
            className="rounded border px-1.5 py-0.5 text-2xs outline-none focus:border-[#10a37f] disabled:opacity-50"
            style={{
              borderColor: "#d4d4d4",
              backgroundColor: "#ffffff",
              color: "#202124",
            }}
          >
            <option value="">全部</option>
            {subItems.map((i) => (
              <option key={i.id} value={i.id}>
                {i.label}
              </option>
            ))}
          </select>
        </label>
        <label
          className="flex items-center gap-1 text-2xs"
          style={{ color: "#6b7280" }}
        >
          发起日期
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="rounded border px-1.5 py-0.5 text-2xs outline-none focus:border-[#10a37f]"
            style={{
              borderColor: "#d4d4d4",
              backgroundColor: "#ffffff",
              color: "#202124",
            }}
          />
          <span style={{ color: "#9ca3af" }}>至</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="rounded border px-1.5 py-0.5 text-2xs outline-none focus:border-[#10a37f]"
            style={{
              borderColor: "#d4d4d4",
              backgroundColor: "#ffffff",
              color: "#202124",
            }}
          />
        </label>
        <label
          className="flex items-center gap-1 text-2xs"
          style={{ color: "#6b7280" }}
        >
          关键词
          <input
            type="text"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyFilters();
            }}
            placeholder="业务名称 / 小类"
            className="w-40 rounded border px-1.5 py-0.5 text-2xs outline-none focus:border-[#10a37f]"
            style={{
              borderColor: "#d4d4d4",
              backgroundColor: "#ffffff",
              color: "#202124",
            }}
          />
        </label>
        <button
          type="button"
          onClick={applyFilters}
          className="btn btn-sm btn-primary"
        >
          查询
        </button>
        <button
          type="button"
          onClick={() => {
            setKeyword("");
            setDateFrom("");
            setDateTo("");
            resetQuery();
          }}
          className="btn btn-sm"
          title="清空全部筛选条件"
        >
          重置
        </button>
        {error && (
          <span className="text-2xs" style={{ color: "#cd3131" }}>
            ⚠ {error}
          </span>
        )}
      </div>

      {/* 结果表 */}
      <div className="min-h-0 flex-1 overflow-auto">
        {tasks.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-2xs">
            <span style={{ color: "#9ca3af" }}>
              {loading ? "正在加载业务台账…" : "没有符合条件的业务"}
            </span>
            {!loading && (
              <span style={{ color: "#b8b5b2" }}>
                业务在运营模式上传第一份材料时自动登记到这里
              </span>
            )}
          </div>
        ) : (
          <table className="w-full border-collapse text-2xs">
            <thead>
              <tr style={{ backgroundColor: "#fafaf9", color: "#6b7280" }}>
                <th className="border-b px-3 py-1.5 text-left font-semibold">
                  业务名称
                </th>
                <th className="border-b px-3 py-1.5 text-left font-semibold">
                  业务大类
                </th>
                <th className="border-b px-3 py-1.5 text-left font-semibold">
                  小类
                </th>
                <th className="border-b px-3 py-1.5 text-left font-semibold">
                  状态
                </th>
                <th className="border-b px-3 py-1.5 text-left font-semibold">
                  发起时间
                </th>
                <th className="border-b px-3 py-1.5 text-left font-semibold">
                  最后更新
                </th>
                <th className="border-b px-3 py-1.5 text-left font-semibold">
                  经办人
                </th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => {
                const meta =
                  OPS_TASK_STATUS_META[t.status] ??
                  OPS_TASK_STATUS_META.in_progress;
                const expanded = expandedId === t.id;
                return (
                  <Fragment key={t.id}>
                    <tr
                      onClick={() => onToggleRow(t)}
                      className="cursor-pointer hover:bg-[#f5f8f7]"
                      style={{ borderBottom: "1px solid #f3f2f1" }}
                    >
                      <td className="px-3 py-1.5" style={{ color: "#202124" }}>
                        <span className="flex items-center gap-1">
                          <span style={{ color: "#9ca3af" }}>
                            {expanded ? "▾" : "▸"}
                          </span>
                          {t.title || t.feature_label || t.case_id}
                        </span>
                      </td>
                      <td className="px-3 py-1.5" style={{ color: "#4b5563" }}>
                        {t.category_label || "—"}
                      </td>
                      <td className="px-3 py-1.5" style={{ color: "#4b5563" }}>
                        {t.feature_label || "—"}
                      </td>
                      <td className="px-3 py-1.5">
                        <span
                          className="rounded-full px-1.5 py-0.5 font-semibold"
                          style={{
                            backgroundColor: `${meta.color}18`,
                            color: meta.color,
                          }}
                        >
                          {meta.icon} {meta.label}
                        </span>
                      </td>
                      <td className="px-3 py-1.5" style={{ color: "#6b7280" }}>
                        {fmtTime(t.created_at)}
                      </td>
                      <td className="px-3 py-1.5" style={{ color: "#6b7280" }}>
                        {fmtTime(t.updated_at)}
                      </td>
                      <td className="px-3 py-1.5" style={{ color: "#6b7280" }}>
                        {t.operator || "—"}
                      </td>
                    </tr>
                    {expanded && (
                      <tr>
                        <td
                          colSpan={7}
                          className="px-3 py-2"
                          style={{ backgroundColor: "#fbfbfa" }}
                        >
                          {detail && detail.task.id === t.id ? (
                            <div className="space-y-1.5">
                              {detail.pending_items.length > 0 && (
                                <div>
                                  <div
                                    className="mb-0.5 font-semibold"
                                    style={{ color: "#cd3131" }}
                                  >
                                    待人工处理（{detail.pending_items.length}）
                                  </div>
                                  {detail.pending_items.map((p, i) => (
                                    <div
                                      key={`${p.file_id}-${i}`}
                                      className="border-l-2 pl-1.5"
                                      style={{
                                        borderColor: "#cd3131",
                                        color: "#4b5563",
                                      }}
                                    >
                                      {p.file_name}：{p.note}
                                    </div>
                                  ))}
                                </div>
                              )}
                              <div style={{ color: "#6b7280" }}>
                                材料进度：共 {detail.file_total} 份
                                {Object.entries(detail.file_stats)
                                  .map(([k, v]) => ` ${k} ${v}`)
                                  .join(" ·")}
                              </div>
                              {/* 凭证摘要（2026-11-09）：齐备度 + 影像回看入口 */}
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span style={{ color: "#6b7280" }}>
                                  凭证：
                                  {detail.credentials &&
                                  detail.credentials.required_total > 0
                                    ? `必传 ${detail.credentials.required_ok}/${detail.credentials.required_total} 已齐`
                                    : "该业务未预设凭证清单"}
                                  {detail.credentials &&
                                  detail.credentials.asset_total > 0
                                    ? ` · 已归档 ${detail.credentials.asset_total} 份`
                                    : ""}
                                  {detail.credentials &&
                                  detail.credentials.unassigned_total > 0
                                    ? ` · 未归属 ${detail.credentials.unassigned_total}`
                                    : ""}
                                </span>
                                {detail.credentials?.missing.length ? (
                                  <span style={{ color: "#cd3131" }}>
                                    缺：
                                    {detail.credentials.missing
                                      .map((m) => m.credential)
                                      .join("、")}
                                  </span>
                                ) : null}
                                {t.case_id && (
                                  <button
                                    type="button"
                                    onClick={() =>
                                      setCredentialView({
                                        caseId: t.case_id,
                                        readOnly:
                                          t.status === "closed" ||
                                          t.status === "suspended",
                                      })
                                    }
                                    className="btn btn-secondary btn-xs"
                                    title="打开凭证预览：影像、已采要素与缺哪一张"
                                  >
                                    🪪 凭证预览
                                  </button>
                                )}
                              </div>
                              {t.status_note && (
                                <div style={{ color: "#b25c1a" }}>
                                  状态说明：{t.status_note}
                                </div>
                              )}
                              {detail.record && (
                                <div style={{ color: "#6b7280" }}>
                                  业务小结：
                                  {detail.record.title || detail.record.id}
                                </div>
                              )}
                            </div>
                          ) : (
                            <span style={{ color: "#9ca3af" }}>
                              正在加载明细…
                            </span>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {credentialView && (
        <CredentialPreviewModal
          caseId={credentialView.caseId}
          teamIds={NO_TEAM_IDS}
          readOnly={credentialView.readOnly}
          onClose={() => setCredentialView(null)}
        />
      )}
    </div>
  );
}
