/**
 * lib/optionReply.ts —— 选项编号「单字回复」确定性扩展（BUGFIX #150，2026-08-26）。
 *
 * 场景：助手给出选项式追问（A/B/C 或 1/2/3）但未成卡时，用户手打「A」回复，
 * 模型常无法把孤立编号与上一轮选项关联（真实翻车：模型只收到「A」→ 回「信息
 * 不太完整」并丢失整个任务上下文）。发送时若回复是孤立编号、且最近一条
 * assistant 消息正文能解析出对应选项，确定性扩展为结构化确认文本（与
 * ClarifyCard 回发同格式 → 复用 native 循环「[回答确认问题]」任务背景加固链，
 * BUGFIX #140）；解析不出 / 非孤立编号一律原样返回，绝不动用户原文。
 *
 * 与后端 responder._ENUM_PATTERNS 镜像：行首允许列表符号 / markdown 加粗包裹。
 */

/** 孤立编号回复：可选「选/选项」前缀 + 单个字母或 1-2 位数字 + 可选尾部分隔符 */
const BARE_MARKER_RE = /^\s*(?:选项|选)?\s*([A-Ia-i]|\d{1,2})\s*[．.、)）:：]?\s*$/;

/** 正文选项行：列表符号 / 加粗前缀 + 字母或数字标记 + 分隔符 + 文本 */
const LINE_OPT_RE =
  /^\s*(?:[-*•]\s*)?(?:\*{1,2}\s*)?(?:选项\s*)?([A-Ia-i]|\d{1,2})\s*[．.、)）:：]\s*(.+)$/;

/** 选择引导语（镜像后端 _CHOICE_CUE_RE）：无引导语的编号列表（如操作步骤）不扩展 */
const CHOICE_CUE_RE =
  /回复\s*(编号|数字|字母)|请选择|候选方案|任选其一|任一|选\s*(一|哪)|挑\s*一|选项|多选/;

export function expandOptionReply(reply: string, lastAssistantContent: string): string {
  const m = BARE_MARKER_RE.exec(reply);
  if (!m || !lastAssistantContent) return reply;
  const marker = m[1];

  // clarify 围栏剥掉再找（卡片路径本就结构化回发，这里兜底手打场景）
  const body = lastAssistantContent.replace(/```clarify\s*[\s\S]*?```/, '');

  // 全量扫一遍：既找目标编号的文本，也确认是否出现过「选项X」格式（自带选择语义）
  const hits: Array<[string, string]> = [];
  let hitOptionFormat = false;
  for (const line of body.split('\n')) {
    const lm = LINE_OPT_RE.exec(line);
    if (!lm) continue;
    if (/(?:^|[-*•]\s*)(?:\*{1,2}\s*)?选项/.test(line)) hitOptionFormat = true;
    hits.push([lm[1], lm[2].replace(/[*`]/g, '').trim()]);
  }
  const found = hits.find(([mk]) => mk.toLowerCase() === marker.toLowerCase())?.[1] ?? '';
  if (!found) return reply;
  // 「选项X」格式自带选择语义；其余编号列表须有引导语，防操作步骤被误扩展
  if (!hitOptionFormat && !CHOICE_CUE_RE.test(body)) return reply;

  const display = /^[a-iA-I]$/.test(marker) ? marker.toUpperCase() : marker;
  return `[回答确认问题]\n1. 选项 ${display} → ${found}\n请按以上选择继续。`;
}
