/**
 * fileRef —— 📄 文件引用识别（架构师忠告 2：正则匹配，不确定不强行高亮）。
 *
 * 匹配 `📄 filename.ext` 格式，后缀限制为常见编程语言/配置文件后缀，
 * 避免把普通中文句子误识别为文件。
 */

/** 常见后缀白名单（不确定的路径不强行高亮） */
const KNOWN_EXT =
  '(?:py|ts|tsx|js|jsx|vue|svelte|java|kt|go|rs|c|cpp|h|hpp|cs|rb|php|sql|sh|bat|ps1|yaml|yml|json|toml|ini|xml|md|txt|html|css|scss|less|csv|log|conf|properties)';

/** 📄 后跟文件名（允许路径分隔符），后缀必须在白名单内 */
export const FILE_REF_RE = new RegExp(
  `📄\\s*([\\w./\\\\-]+\\.${KNOWN_EXT})`,
  'g',
);

export interface FileRefSegment {
  text: string;
  /** 命中的文件路径（非文件片段为 null） */
  file: string | null;
}

/** 把思考文本切成 [普通文本 | 文件引用] 片段序列。 */
export function splitFileRefs(text: string): FileRefSegment[] {
  const segments: FileRefSegment[] = [];
  let last = 0;
  FILE_REF_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = FILE_REF_RE.exec(text)) !== null) {
    if (m.index > last) {
      segments.push({ text: text.slice(last, m.index), file: null });
    }
    segments.push({ text: m[0], file: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    segments.push({ text: text.slice(last), file: null });
  }
  return segments;
}

/** 文件名（basename）展示用。 */
export function baseName(path: string): string {
  const norm = path.replace(/\\/g, '/');
  return norm.split('/').pop() ?? path;
}
