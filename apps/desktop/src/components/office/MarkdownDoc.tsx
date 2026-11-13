/**
 * MarkdownDoc —— Markdown 文档渲染（frontmatter 元数据卡 + 正文），2026-11-09。
 *
 * 预览面板（OfficePreviewPanel）用它替代裸 `Markdown`：文档顶部的 YAML 头
 * 会被抽出来渲染成元数据卡，正文只渲染 Markdown 本体。
 * 无 frontmatter 的普通 md 走老路径，渲染结果与之前完全一致。
 */
import { useMemo } from "react";
import { Markdown } from "@/components/chat/Markdown";
import { DocMetaCard } from "./DocMetaCard";
import { parseFrontmatter, fmText } from "@/lib/frontmatter";

/**
 * 正文首行是加粗标题、且与 frontmatter 标题重复时去掉那一行
 * （法规文档正文第一行通常是 `**条例名**`，否则标题会显示两遍）。
 */
function stripLeadingTitle(body: string, title: string): string {
  if (title === "") return body;
  const lines = body.split("\n");
  let i = 0;
  while (i < lines.length && lines[i].trim() === "") i += 1;
  const m = /^\*\*(.+)\*\*$/.exec(lines[i]?.trim() ?? "");
  if (m && m[1].trim() === title.trim()) {
    return lines.slice(i + 1).join("\n").replace(/^\n+/, "");
  }
  return body;
}

/** Markdown 文档渲染入口（含 frontmatter 元数据卡） */
export function MarkdownDoc({ text }: { text: string }): JSX.Element {
  const parsed = useMemo(() => parseFrontmatter(text), [text]);
  const title = parsed.found ? fmText(parsed.data, "title") || fmText(parsed.data, "LinkTitle") : "";
  const body = useMemo(() => stripLeadingTitle(parsed.body, title), [parsed.body, title]);

  return (
    <>
      {parsed.found && <DocMetaCard data={parsed.data} />}
      <Markdown text={body} />
    </>
  );
}
