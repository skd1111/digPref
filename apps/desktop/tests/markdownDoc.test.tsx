/**
 * Markdown 文档渲染（frontmatter 元数据卡）测试（2026-11-09）。
 *
 * 背景：知识库法规文档顶部带 YAML 头，此前按正文原样渲染（字段挤成一行 +
 * 裸圆点列表 + URL 被路径胶囊截断），观感极差。
 * 覆盖：元数据卡结构 / YAML 原文不再泄漏 / 链接可点 / 收起展开 /
 *       无 frontmatter 时保持旧行为 / 正文重复标题去重。
 */
import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MarkdownDoc } from '@/components/office/MarkdownDoc';

const DOC = [
  '---',
  'title: 防范和处置非法集资条例',
  'LinkTitle: 防范和处置非法集资条例（2021）',
  'author: 国务院',
  'publication_date: 2021-01-26',
  'effective_date: 2021-05-01',
  'status: 有效',
  'group: 行政法规',
  'categories:',
  '  - 行政法规',
  'tags:',
  '  - 国务院',
  '  - 案例',
  'urls:',
  '  - https://flk.npc.gov.cn/detail?id=ff808081777d07c50177b8d8670038d4',
  '---',
  '',
  '**防范和处置非法集资条例**',
  '',
  '第一章　总则',
].join('\n');

describe('MarkdownDoc 元数据卡', () => {
  it('渲染标题、日期与状态徽标', () => {
    render(<MarkdownDoc text={DOC} />);
    expect(screen.getByText('防范和处置非法集资条例')).toBeTruthy();
    expect(screen.getByText('2021-01-26')).toBeTruthy();
    expect(screen.getByText('2021-05-01')).toBeTruthy();
    expect(screen.getByText('有效')).toBeTruthy();
    expect(screen.getByText('国务院 · 行政法规')).toBeTruthy();
  });

  it('分类与标签渲染为胶囊', () => {
    const { container } = render(<MarkdownDoc text={DOC} />);
    const chips = Array.from(container.querySelectorAll('.doc-meta-chip')).map((c) => c.textContent);
    expect(chips).toContain('行政法规');
    expect(chips).toContain('案例');
    expect(container.querySelectorAll('.doc-meta-chip-accent').length).toBeGreaterThan(0);
  });

  it('YAML 原文不再渲染成正文（字段名不外泄）', () => {
    const { container } = render(<MarkdownDoc text={DOC} />);
    expect(container.textContent).not.toContain('publication_date:');
    expect(container.textContent).not.toContain('LinkTitle:');
  });

  it('frontmatter 的 urls 字段不展示（本地文档无外链需求）', () => {
    const { container } = render(<MarkdownDoc text={DOC} />);
    expect(container.querySelector('a.doc-meta-link')).toBeNull();
    expect(container.textContent).not.toContain('flk.npc.gov.cn');
    // 关键回归：URL 也不应被渲染成「文件路径胶囊」
    expect(container.querySelector('.md-path-chip')).toBeNull();
  });

  it('正文正常渲染，且与 frontmatter 重复的加粗标题只出现一次', () => {
    render(<MarkdownDoc text={DOC} />);
    // 注：getByText 只归一化节点文本、不归一化查询串，全角空格需用正则匹配
    expect(screen.getByText(/第一章\s*总则/)).toBeTruthy();
    expect(screen.getAllByText('防范和处置非法集资条例')).toHaveLength(1);
  });

  it('收起 / 展开切换元数据体', () => {
    const { container } = render(<MarkdownDoc text={DOC} />);
    expect(container.querySelector('.doc-meta-body')).not.toBeNull();
    fireEvent.click(screen.getByText('收起 ▴'));
    expect(container.querySelector('.doc-meta-body')).toBeNull();
    fireEvent.click(screen.getByText('展开 ▾'));
    expect(container.querySelector('.doc-meta-body')).not.toBeNull();
  });

  it('无 frontmatter 的普通 md 不出现元数据卡', () => {
    const { container } = render(<MarkdownDoc text={'# 普通文档\n\n正文内容'} />);
    expect(container.querySelector('.doc-meta')).toBeNull();
    expect(container.textContent).toContain('普通文档');
    expect(container.textContent).toContain('正文内容');
  });

  it('未知字段进「其他信息」网格（键名原样展示）', () => {
    const { container } = render(
      <MarkdownDoc text={'---\ntitle: 标题\ndoc_id: abc123\n---\n正文'} />,
    );
    expect(container.textContent).toContain('doc_id');
    expect(container.textContent).toContain('abc123');
  });
});
