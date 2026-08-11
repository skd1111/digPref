/**
 * Markdown 轻量渲染器测试（2026-08-07）。
 *
 * 覆盖：标题 / 列表 / 围栏代码块 / 行内语法 / 表格 / 链接安全白名单。
 * 渲染走 React 元素树（无 dangerouslySetInnerHTML），天然无 XSS 注入面，
 * 这里重点验证解析正确性与链接协议白名单。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Markdown } from '@/components/chat/Markdown';

describe('Markdown 块级解析', () => {
  it('标题渲染为对应 h 标签', () => {
    const { container } = render(<Markdown text={'## 小节标题'} />);
    const h2 = container.querySelector('h2');
    expect(h2?.textContent).toBe('小节标题');
  });

  it('无序列表逐项渲染', () => {
    const text = ['- 第一项', '- 第二项'].join('\n');
    const { container } = render(<Markdown text={text} />);
    const items = container.querySelectorAll('ul li');
    expect(items.length).toBe(2);
    expect(items[0]?.textContent).toBe('第一项');
  });

  it('有序列表渲染为 ol', () => {
    const text = ['1. 步骤一', '2. 步骤二'].join('\n');
    const { container } = render(<Markdown text={text} />);
    expect(container.querySelector('ol')).toBeTruthy();
    expect(container.querySelectorAll('ol li').length).toBe(2);
  });

  it('围栏代码块渲染为 pre 且不泄露围栏符号', () => {
    const text = ['```sql', 'SELECT 1;', '```'].join('\n');
    const { container } = render(<Markdown text={text} />);
    const pre = container.querySelector('.md-code pre');
    expect(pre?.textContent).toBe('SELECT 1;');
  });

  it('表格渲染表头与数据行', () => {
    const text = ['| 列A | 列B |', '| --- | --- |', '| 1 | 2 |'].join('\n');
    const { container } = render(<Markdown text={text} />);
    expect(container.querySelectorAll('th').length).toBe(2);
    expect(container.querySelectorAll('td').length).toBe(2);
  });

  it('引用块渲染为 blockquote', () => {
    const { container } = render(<Markdown text={'> 注意事项'} />);
    expect(container.querySelector('blockquote')?.textContent).toContain('注意事项');
  });
});

describe('Markdown 行内语法', () => {
  it('加粗渲染为 strong', () => {
    const { container } = render(<Markdown text={'这是**重点**内容'} />);
    expect(container.querySelector('strong')?.textContent).toBe('重点');
  });

  it('行内代码渲染为 code', () => {
    const { container } = render(<Markdown text={'执行 `npm install` 即可'} />);
    expect(container.querySelector('code.md-ic')?.textContent).toBe('npm install');
  });

  it('http 链接正常渲染', () => {
    const { container } = render(<Markdown text={'见 [文档](https://example.com)'} />);
    const a = container.querySelector('a.md-link');
    expect(a?.getAttribute('href')).toBe('https://example.com');
  });

  it('javascript: 协议链接被白名单拦截', () => {
    const { container } = render(
      <Markdown text={'点 [陷阱](javascript:alert(1))'} />,
    );
    const a = container.querySelector('a.md-link');
    expect(a?.getAttribute('href')).toBe('#');
  });
});

describe('Markdown 降级兜底', () => {
  it('纯文本按段落渲染', () => {
    render(<Markdown text={'你好，世界'} />);
    expect(screen.getByText('你好，世界')).toBeTruthy();
  });

  it('未闭合围栏不崩溃（文件截断场景）', () => {
    const text = ['```', 'SELECT 1;'].join('\n');
    const { container } = render(<Markdown text={text} />);
    expect(container.querySelector('.md-code pre')?.textContent).toBe('SELECT 1;');
  });
});
