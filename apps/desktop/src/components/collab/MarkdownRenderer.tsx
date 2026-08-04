/**
 * MarkdownRenderer —— Phase 9 评论内容渲染（V0 MVP，轻量自实现 + DOMPurify XSS 防护）。
 *
 * 范围（V0 简化）：
 *   - 段落 / 换行
 *   - **bold** / *italic* / `code`
 *   - 代码块（```lang ... ```）
 *   - [text](url) 链接
 *   - @user_id 高亮
 *   - 列表 - / 1.
 *
 * 后续 Phase 9 V1 可替换为 `react-markdown + remark-gfm + rehype-sanitize` 三件套。
 */
import { useMemo } from 'react';
import DOMPurify from 'dompurify';
import { USER_BY_ID } from '@/types/collab';

interface MarkdownRendererProps {
  content: string;
  /** 密文占位提示（如果 content 是 [encrypted:...] 整串） */
  isEncryptedPlaceholder?: boolean;
}

/**
 * 极简 Markdown → HTML 转换器。
 * 实现的子集：粗体、斜体、行内 code、代码块、链接、有序/无序列表、@提及。
 * 输出经过 DOMPurify sanitize 防 XSS。
 */
function toHtml(input: string): string {
  // 1. 转义 HTML 特殊字符（除 placeholder 标记外）
  const esc = (s: string): string =>
    s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

  const lines = input.split(/\r?\n/);
  const out: string[] = [];
  let inCode = false;
  let codeLang = '';
  let codeBuf: string[] = [];
  let listMode: 'ul' | 'ol' | null = null;
  let paraBuf: string[] = [];

  const flushPara = (): void => {
    if (paraBuf.length === 0) return;
    const text = paraBuf.join('\n');
    out.push(`<p>${inline(text)}</p>`);
    paraBuf = [];
  };
  const flushList = (): void => {
    if (!listMode) return;
    out.push(`</${listMode}>`);
    listMode = null;
  };

  const inline = (s: string): string => {
    // 先 @ 提及（在 escape 之前做，便于识别整词）
    let r = esc(s);
    // @user_id → <a class="mention">@name</a>
    r = r.replace(
      /@([a-z0-9_-]+)/g,
      (_, uid) => {
        const u = USER_BY_ID[uid];
        const label = u ? u.name : uid;
        return `<a class="mention" data-user="${uid}">@${label}</a>`;
      },
    );
    // 链接 [text](url)
    r = r.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a class="link" href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
    // 行内 code
    r = r.replace(/`([^`\n]+)`/g, '<code class="inline">$1</code>');
    // 粗体
    r = r.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    // 斜体
    r = r.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    return r;
  };

  for (const raw of lines) {
    if (raw.trim().startsWith('```')) {
      flushPara();
      flushList();
      if (inCode) {
        out.push(`<pre class="code-block"><code class="lang-${codeLang}">${esc(codeBuf.join('\n'))}</code></pre>`);
        inCode = false;
        codeBuf = [];
      } else {
        inCode = true;
        codeLang = raw.trim().slice(3).trim() || 'text';
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(raw);
      continue;
    }
    // 列表
    const ulMatch = raw.match(/^\s*[-*]\s+(.*)/);
    const olMatch = raw.match(/^\s*\d+\.\s+(.*)/);
    if (ulMatch || olMatch) {
      flushPara();
      const want = ulMatch ? 'ul' : 'ol';
      if (listMode !== want) {
        flushList();
        out.push(`<${want}>`);
        listMode = want;
      }
      out.push(`<li>${inline((ulMatch ?? olMatch)![1])}</li>`);
      continue;
    } else {
      flushList();
    }
    if (raw.trim() === '') {
      flushPara();
      continue;
    }
    paraBuf.push(raw);
  }
  flushPara();
  flushList();
  if (inCode) {
    out.push(`<pre class="code-block"><code class="lang-${codeLang}">${esc(codeBuf.join('\n'))}</code></pre>`);
  }
  return out.join('\n');
}

export function MarkdownRenderer({ content, isEncryptedPlaceholder }: MarkdownRendererProps): JSX.Element {
  const html = useMemo(() => {
    if (isEncryptedPlaceholder) {
      return `<div class="encrypted-placeholder"><span class="lock">🔒</span> 该评论已由后端 AES-256-GCM 加密（占位）</div>`;
    }
    const raw = toHtml(content);
    return DOMPurify.sanitize(raw, {
      ALLOWED_TAGS: [
        'p', 'br', 'strong', 'em', 'code', 'pre',
        'a', 'ul', 'ol', 'li', 'span', 'div',
      ],
      ALLOWED_ATTR: ['class', 'href', 'target', 'rel', 'data-user'],
    });
  }, [content, isEncryptedPlaceholder]);

  return (
    <div
      className="collab-md text-ui leading-relaxed"
      // 关键：HTML 已过 DOMPurify
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
