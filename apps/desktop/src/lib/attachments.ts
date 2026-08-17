/**
 * attachments —— chat 附加文件上下文（2026-08-14）。
 *
 * ChatInput 📎 按钮选中文件后：FileReader 读成 base64 → Rust chat_attach_file
 * → 后端 /chat/attach-file 转文本（文本/代码直读，docx/pdf 等走 file_to_markdown）。
 * 发送时 buildAttachmentsSnippet 把已就绪的附件拼成 prompt 上下文段。
 *
 * 上限与 Rust agent_chat 的 100KB prompt 闸门对齐：
 *   - 单文件 ≤ 12000 字符（后端已截断）
 *   - 最多 MAX_ATTACHMENTS 个文件，拼接总量 ≤ MAX_SNIPPET_CHARS
 */

/** 单次对话最多附加的文件数 */
export const MAX_ATTACHMENTS = 5;
/** 附件拼接段总字符上限（防 prompt 超 Rust 100KB 闸门） */
export const MAX_SNIPPET_CHARS = 40_000;

/** 附加文件在 UI 中的状态机 */
export interface ChatAttachment {
  id: string;
  name: string;
  /** uploading=转换中；ready=可拼 prompt；error=转换失败（error 有值） */
  status: 'uploading' | 'ready' | 'error';
  /** 转换后的文本内容（原文或 Markdown） */
  content: string;
  /** 后端报告的原字符数（截断前） */
  chars: number;
  /** 后端是否做了截断 */
  truncated: boolean;
  /** mode=text 原文 / markdown 转换 */
  mode: 'text' | 'markdown';
  error: string;
}

/** FileReader 读文件为纯 base64（去掉 dataURL 前缀）；与 opsCaseStore 同模式 */
export function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? '');
      const idx = result.indexOf(',');
      resolve(idx >= 0 ? result.slice(idx + 1) : result);
    };
    reader.onerror = () => reject(new Error('文件读取失败'));
    reader.readAsDataURL(file);
  });
}

/**
 * 把就绪的附件拼成 prompt 上下文段；没有可用附件返 null。
 * 总量超 MAX_SNIPPET_CHARS 时停止追加并附注说明。
 */
export function buildAttachmentsSnippet(attachments: ChatAttachment[]): string | null {
  const ready = attachments.filter((a) => a.status === 'ready' && a.content);
  if (ready.length === 0) return null;

  const parts: string[] = ['【用户附加文件内容】'];
  let total = 0;
  let included = 0;
  for (const a of ready) {
    const header = `\n--- 附件${included + 1}：${a.name}${a.mode === 'markdown' ? '（已转 Markdown）' : ''}${
      a.truncated ? '（内容过长已截断）' : ''
    } ---\n`;
    if (total + header.length + a.content.length > MAX_SNIPPET_CHARS && included > 0) {
      parts.push(`\n（其余 ${ready.length - included} 个附件因长度限制未包含）`);
      break;
    }
    parts.push(header + a.content);
    total += header.length + a.content.length;
    included += 1;
  }
  return parts.join('');
}
