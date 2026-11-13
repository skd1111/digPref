/**
 * DocMetaCard —— Markdown frontmatter 元数据卡（2026-11-09）。
 *
 * 解决的问题：知识库法规文档顶部的 YAML 头此前按正文原样渲染
 * （`title: xxx author: yyy ...` 挤在一行 + 裸圆点列表 + URL 被截断），观感极差。
 * 本组件把 frontmatter 渲染成一张结构化卡片：
 *   标题 + 效力状态徽标 + 制定机关/法规层级 + 日期网格 + 分类/标签胶囊。
 *
 * 纯展示组件：无副作用、无 IPC。
 * `urls`（法规原文外链）不展示 —— 文档都在本地，外链对用户无意义。
 */
import { useState } from "react";
import { fmItems, fmText, type FrontmatterData } from "@/lib/frontmatter";

/** 已知字段 → 中文标签（未列出的键直接用原键名兜底） */
const FIELD_LABELS: Record<string, string> = {
  title: "标题",
  linktitle: "标题",
  author: "制定机关",
  publication_date: "发布日期",
  effective_date: "施行日期",
  status: "效力状态",
  group: "法规层级",
  categories: "分类",
  tags: "标签",
};

/** 已被卡片消费的字段（其余键进「其他信息」网格；urls = 本地文档无外链需求，不展示） */
const CONSUMED_KEYS = new Set([
  "title",
  "linktitle",
  "author",
  "publication_date",
  "effective_date",
  "status",
  "group",
  "categories",
  "tags",
  "urls",
]);

/** 效力状态：含「失效/废止」→ 灰色；含「有效」→ 绿色徽标 */
function statusTone(status: string): string {
  if (!status) return "doc-meta-chip";
  if (/失效|废止/.test(status)) return "doc-meta-chip-off";
  if (/有效/.test(status)) return "doc-meta-chip-ok";
  return "doc-meta-chip";
}

/** 键 → 标签（大小写不敏感） */
function labelOf(key: string): string {
  return FIELD_LABELS[key.toLowerCase()] ?? key;
}

/** 单个「标签: 值」字段 */
function MetaField({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="doc-meta-field">
      <span className="doc-meta-label">{label}</span>
      <span className="doc-meta-value">{value}</span>
    </div>
  );
}

/** 元数据卡（数据来源：Markdown frontmatter） */
export function DocMetaCard({ data }: { data: FrontmatterData }): JSX.Element {
  const [collapsed, setCollapsed] = useState(false);

  const title = fmText(data, "title") || fmText(data, "LinkTitle");
  const author = fmText(data, "author");
  const group = fmText(data, "group");
  const status = fmText(data, "status");
  const publicationDate = fmText(data, "publication_date");
  const effectiveDate = fmText(data, "effective_date");
  const categories = fmItems(data, "categories");
  const tags = fmItems(data, "tags");

  // 未被消费的字段 → 「其他信息」网格
  const extras: Array<{ key: string; label: string; value: string }> = [];
  for (const [key, value] of Object.entries(data)) {
    if (CONSUMED_KEYS.has(key.toLowerCase())) continue;
    const text = Array.isArray(value) ? value.join("、") : value;
    if (text === "") continue;
    extras.push({ key, label: labelOf(key), value: text });
  }

  const subtitle = [author, group].filter((s) => s !== "").join(" · ");
  const dates = [
    { label: "发布日期", value: publicationDate },
    { label: "施行日期", value: effectiveDate },
  ].filter((d) => d.value !== "");

  return (
    <section className="doc-meta" aria-label="文档元数据">
      <div className="doc-meta-head">
        <div className="min-w-0">
          <div className="doc-meta-title">{title || "未命名文档"}</div>
          {subtitle !== "" && <div className="doc-meta-sub">{subtitle}</div>}
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          {status !== "" && (
            <span className={`doc-meta-chip ${statusTone(status)}`}>{status}</span>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-xs"
            onClick={() => setCollapsed((v) => !v)}
            title={collapsed ? "展开文档元数据" : "收起文档元数据"}
          >
            {collapsed ? "展开 ▾" : "收起 ▴"}
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="doc-meta-body">
          {dates.length > 0 && (
            <div className="doc-meta-grid">
              {dates.map((d) => (
                <MetaField key={d.label} label={d.label} value={d.value} />
              ))}
            </div>
          )}

          {extras.length > 0 && (
            <div className="doc-meta-grid">
              {extras.map((e) => (
                <MetaField key={e.key} label={e.label} value={e.value} />
              ))}
            </div>
          )}

          {categories.length > 0 && (
            <div className="doc-meta-row">
              <span className="doc-meta-label">分类</span>
              <div className="doc-meta-chips">
                {categories.map((c) => (
                  <span key={c} className="doc-meta-chip doc-meta-chip-accent">
                    {c}
                  </span>
                ))}
              </div>
            </div>
          )}

          {tags.length > 0 && (
            <div className="doc-meta-row">
              <span className="doc-meta-label">标签</span>
              <div className="doc-meta-chips">
                {tags.map((t) => (
                  <span key={t} className="doc-meta-chip">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
