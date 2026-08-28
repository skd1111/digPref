/**
 * lib/clarify.ts —— 模型「选项式追问」解析（2026-08-05）。
 *
 * 模型需要用户确认时，按系统提示词约定在回复末尾输出 ```clarify 围栏块
 * （JSON 数组：每题 3-5 个选项、带理由、恰好一个推荐项）。
 * 本模块负责：
 *   - parseClarifyBlock：从 assistant 消息正文提取结构化问题列表
 *   - stripClarifyBlock：渲染消息时剥离围栏块（用户不见原始 JSON）
 */

export interface ClarifyOption {
  text: string;
  reason: string;
  recommended: boolean;
}

export interface ClarifyQuestion {
  question: string;
  options: ClarifyOption[];
  /** 多选标记（BUGFIX #149）：true 时前端渲染复选框、可选多项；旧数据无此字段按单选 */
  multi?: boolean;
}

export interface ClarifyParseResult {
  /** 剥离选项块后的正文（渲染用） */
  text: string;
  questions: ClarifyQuestion[];
}

const CLARIFY_FENCE = /```clarify\s*([\s\S]*?)```/;
/** 每题选项上限（与提示词约定一致，防模型超发） */
const MAX_OPTIONS = 5;

export function parseClarifyBlock(content: string): ClarifyParseResult | null {
  if (!content) return null;
  const m = content.match(CLARIFY_FENCE);
  if (!m) return null;

  const raw = m[1].trim();
  const start = raw.indexOf('[');
  const end = raw.lastIndexOf(']');
  if (start === -1 || end <= start) return null;

  let arr: unknown;
  try {
    arr = JSON.parse(raw.slice(start, end + 1));
  } catch {
    return null;
  }
  if (!Array.isArray(arr)) return null;

  const questions: ClarifyQuestion[] = [];
  for (const item of arr) {
    if (typeof item !== 'object' || item === null) continue;
    const q = item as Record<string, unknown>;
    const question = typeof q.question === 'string' ? q.question.trim() : '';
    if (!question || !Array.isArray(q.options)) continue;

    const options: ClarifyOption[] = [];
    for (const o of q.options) {
      if (typeof o !== 'object' || o === null) continue;
      const oo = o as Record<string, unknown>;
      const text = typeof oo.text === 'string' ? oo.text.trim() : '';
      if (!text) continue;
      options.push({
        text,
        reason: typeof oo.reason === 'string' ? oo.reason : '',
        recommended: oo.recommended === true,
      });
    }
    if (options.length === 0) continue;
    questions.push({
      question,
      options: options.slice(0, MAX_OPTIONS),
      multi: q.multi === true,
    });
  }
  if (questions.length === 0) return null;

  return { text: content.replace(m[0], '').trim(), questions };
}

/** 渲染消息正文用：剥离 clarify 围栏块；无块时原样返回。
 *  注意：围栏内 JSON 损坏也照样剥离 —— 原始 JSON 永远不该裸露给用户（#149）；
 *  此时 parseClarifyBlock 仍返回 null（不渲染卡片），正文只丢围栏不丢可读部分。 */
export function stripClarifyBlock(content: string): string {
  if (!content) return content;
  const m = content.match(CLARIFY_FENCE);
  if (!m) return content;
  return content.replace(m[0], '').trim();
}
