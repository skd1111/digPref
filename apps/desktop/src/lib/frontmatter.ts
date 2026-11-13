/**
 * frontmatter —— Markdown YAML 头解析（2026-11-09）。
 *
 * 知识库里的法规/制度文档在 md 顶部带一段 `---` 包裹的 YAML 头
 * （title / status / categories / tags / urls …）。通用 Markdown 渲染器把它当正文输出：
 * 字段名与值挤成一行、列表变成裸圆点、URL 还被「文件路径胶囊」截断（见 BUGFIX #201），
 * 观感很差。此模块把这段头解析成结构化数据，供 DocMetaCard 渲染成元数据卡。
 *
 * 支持的 YAML 子集（够用即止，零依赖、不引第三方 YAML 解析器）：
 *   - `key: value`          标量（成对引号会被去掉）
 *   - `key: [a, b]`         行内数组
 *   - `key:` + `- item`     块数组（缩进 0~N 均可）
 *   - `# ...`               注释行忽略
 * 不支持嵌套 map 与多行标量（`|` / `>`）：遇到时按整行原文当字符串处理。
 */

/** frontmatter 字段值：标量字符串或字符串数组 */
export type FrontmatterValue = string | string[];

/** 解析结果：字段表（保留原始大小写键名） */
export interface FrontmatterData {
  [key: string]: FrontmatterValue;
}

export interface ParsedFrontmatter {
  /** 解析出的字段 */
  data: FrontmatterData;
  /** 去掉 frontmatter 后的正文 */
  body: string;
  /** 是否命中 frontmatter（首行是 --- 且存在闭合 ---） */
  found: boolean;
}

const FENCE = "---";

/** 去掉成对引号（'xxx' / "xxx"） */
function unquote(s: string): string {
  const t = s.trim();
  if (t.length >= 2) {
    const first = t[0];
    const last = t[t.length - 1];
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return t.slice(1, -1);
    }
  }
  return t;
}

/** 解析 frontmatter 内的若干行（缩进已忽略） */
function parseBlock(lines: string[]): FrontmatterData {
  const data: FrontmatterData = {};
  let i = 0;
  while (i < lines.length) {
    const trimmed = lines[i].trim();
    // 空行 / 注释行 / 无主列表项 → 跳过
    if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith("- ")) {
      i += 1;
      continue;
    }
    const m = /^([^:#][^:]*):\s*(.*)$/.exec(trimmed);
    if (!m) {
      i += 1;
      continue;
    }
    const key = m[1].trim();
    const rest = m[2].trim();
    if (rest !== "") {
      if (rest.startsWith("[") && rest.endsWith("]")) {
        data[key] = rest
          .slice(1, -1)
          .split(",")
          .map((s) => unquote(s))
          .filter((s) => s !== "");
      } else {
        data[key] = unquote(rest);
      }
      i += 1;
      continue;
    }
    // 值为空 → 收集紧随其后的列表项（块数组）
    const items: string[] = [];
    let j = i + 1;
    while (j < lines.length) {
      const it = lines[j].trim();
      if (it === "") {
        j += 1;
        continue;
      }
      const im = /^-\s*(.*)$/.exec(it);
      if (!im) break;
      items.push(unquote(im[1]));
      j += 1;
    }
    if (items.length > 0) {
      data[key] = items;
      i = j;
      continue;
    }
    data[key] = "";
    i += 1;
  }
  return data;
}

/**
 * 解析 Markdown 文本的 frontmatter。
 * 无 frontmatter 时 found=false，body 为（换行归一后的）原文。
 */
export function parseFrontmatter(raw: string): ParsedFrontmatter {
  const text = (raw ?? "").replace(/\r\n/g, "\n");
  const lines = text.split("\n");
  if (lines.length === 0 || lines[0].trim() !== FENCE) {
    return { data: {}, body: text, found: false };
  }
  let end = -1;
  for (let i = 1; i < lines.length; i += 1) {
    if (lines[i].trim() === FENCE) {
      end = i;
      break;
    }
  }
  // 没有闭合 --- → 不是 frontmatter，整篇按正文处理
  if (end < 0) {
    return { data: {}, body: text, found: false };
  }
  const data = parseBlock(lines.slice(1, end));
  const body = lines.slice(end + 1).join("\n").replace(/^\n+/, "");
  return { data, body, found: true };
}

/** 取字段原始值（键名大小写不敏感，兼容 LinkTitle / linktitle 两种写法） */
export function fmValue(data: FrontmatterData, key: string): FrontmatterValue | undefined {
  const lower = key.toLowerCase();
  for (const [k, v] of Object.entries(data)) {
    if (k.toLowerCase() === lower) return v;
  }
  return undefined;
}

/** 取字段的标量文本（数组取首项） */
export function fmText(data: FrontmatterData, key: string): string {
  const v = fmValue(data, key);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v[0] ?? "";
  return "";
}

/** 取字段的列表值（标量自动包成单元素数组） */
export function fmItems(data: FrontmatterData, key: string): string[] {
  const v = fmValue(data, key);
  if (typeof v === "string") return v === "" ? [] : [v];
  if (Array.isArray(v)) return v;
  return [];
}
