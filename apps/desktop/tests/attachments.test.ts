/**
 * attachments —— buildAttachmentsSnippet / readFileAsBase64 单测（2026-08-14）。
 */
import { describe, expect, it } from 'vitest';
import {
  buildAttachmentsSnippet,
  readFileAsBase64,
  MAX_SNIPPET_CHARS,
  type ChatAttachment,
} from '@/lib/attachments';

function att(over: Partial<ChatAttachment>): ChatAttachment {
  return {
    id: 'a1',
    name: 'file.txt',
    status: 'ready',
    content: 'hello',
    chars: 5,
    truncated: false,
    mode: 'text',
    error: '',
    ...over,
  };
}

describe('buildAttachmentsSnippet', () => {
  it('没有就绪附件返 null', () => {
    expect(buildAttachmentsSnippet([])).toBeNull();
    expect(buildAttachmentsSnippet([att({ status: 'uploading' })])).toBeNull();
    expect(buildAttachmentsSnippet([att({ status: 'error', content: '' })])).toBeNull();
    expect(buildAttachmentsSnippet([att({ content: '' })])).toBeNull();
  });

  it('文本附件拼成上下文段', () => {
    const s = buildAttachmentsSnippet([att({ name: 'foo.py', content: 'def x(): pass' })]);
    expect(s).toContain('【用户附加文件内容】');
    expect(s).toContain('附件1：foo.py');
    expect(s).toContain('def x(): pass');
    expect(s).not.toContain('已转 Markdown');
  });

  it('markdown 模式与截断标注', () => {
    const s = buildAttachmentsSnippet([
      att({ name: 'report.docx', mode: 'markdown', truncated: true }),
    ]);
    expect(s).toContain('已转 Markdown');
    expect(s).toContain('内容过长已截断');
  });

  it('总量超限时丢弃后续附件并附注', () => {
    const big = 'x'.repeat(MAX_SNIPPET_CHARS);
    const s = buildAttachmentsSnippet([
      att({ id: 'a', name: 'big1.txt', content: big }),
      att({ id: 'b', name: 'big2.txt', content: big }),
    ]);
    expect(s).toContain('big1.txt');
    expect(s).not.toContain(`big2.txt（`);
    expect(s).toContain('因长度限制未包含');
  });
});

describe('readFileAsBase64', () => {
  it('读文件为纯 base64（去掉 dataURL 前缀）', async () => {
    const file = new File(['hello'], 'a.txt', { type: 'text/plain' });
    const b64 = await readFileAsBase64(file);
    expect(b64).toBe('aGVsbG8=');
  });
});
