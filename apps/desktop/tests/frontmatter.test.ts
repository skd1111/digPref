/**
 * frontmatter 解析器单测（2026-11-09）。
 *
 * 覆盖：标量 / 行内数组 / 块数组 / 引号 / 注释 / CRLF 归一 /
 *       无 frontmatter / 未闭合围栏 / 键名大小写不敏感。
 */
import { describe, it, expect } from 'vitest';
import {
  parseFrontmatter,
  fmText,
  fmItems,
  fmValue,
} from '@/lib/frontmatter';

const SAMPLE = [
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
  '  - 有效',
  'urls:',
  '  - https://flk.npc.gov.cn/detail?id=ff808081777d07c50177b8d8670038d4',
  '---',
  '',
  '正文第一行',
].join('\n');

describe('parseFrontmatter', () => {
  it('识别 YAML 头并切出正文', () => {
    const r = parseFrontmatter(SAMPLE);
    expect(r.found).toBe(true);
    expect(r.body.startsWith('正文第一行')).toBe(true);
    expect(r.body).not.toContain('publication_date');
  });

  it('标量字段解析为字符串', () => {
    const r = parseFrontmatter(SAMPLE);
    expect(r.data['title']).toBe('防范和处置非法集资条例');
    expect(r.data['status']).toBe('有效');
    expect(r.data['group']).toBe('行政法规');
  });

  it('块数组字段解析为字符串数组', () => {
    const r = parseFrontmatter(SAMPLE);
    expect(r.data['categories']).toEqual(['行政法规']);
    expect(r.data['tags']).toEqual(['国务院', '有效']);
    expect(r.data['urls']).toEqual([
      'https://flk.npc.gov.cn/detail?id=ff808081777d07c50177b8d8670038d4',
    ]);
  });

  it('行内数组解析为字符串数组', () => {
    const r = parseFrontmatter('---\ntags: [甲, 乙]\n---\n正文');
    expect(r.data['tags']).toEqual(['甲', '乙']);
  });

  it('成对引号被去掉', () => {
    const r = parseFrontmatter('---\ntitle: "带引号的标题"\n---\n正文');
    expect(r.data['title']).toBe('带引号的标题');
  });

  it('注释行被忽略', () => {
    const r = parseFrontmatter('---\n# 这是注释\ntitle: 标题\n---\n正文');
    expect(r.data['title']).toBe('标题');
    expect(Object.keys(r.data)).toHaveLength(1);
  });

  it('CRLF 换行被归一', () => {
    const r = parseFrontmatter('---\r\ntitle: 标题\r\n---\r\n正文第一行');
    expect(r.found).toBe(true);
    expect(r.data['title']).toBe('标题');
    expect(r.body).toBe('正文第一行');
  });

  it('无 frontmatter 时原样返回正文', () => {
    const r = parseFrontmatter('# 标题\n\n正文');
    expect(r.found).toBe(false);
    expect(r.data).toEqual({});
    expect(r.body).toBe('# 标题\n\n正文');
  });

  it('未闭合围栏不算 frontmatter（不吞正文）', () => {
    const r = parseFrontmatter('---\ntitle: 标题\n\n正文');
    expect(r.found).toBe(false);
    expect(r.body).toContain('正文');
  });

  it('空字符串不报错', () => {
    const r = parseFrontmatter('');
    expect(r.found).toBe(false);
    expect(r.body).toBe('');
  });
});

describe('字段取值辅助', () => {
  it('fmText 取标量（数组取首项）', () => {
    const data = { title: '标题', tags: ['甲', '乙'] };
    expect(fmText(data, 'title')).toBe('标题');
    expect(fmText(data, 'tags')).toBe('甲');
    expect(fmText(data, 'missing')).toBe('');
  });

  it('fmItems 取列表（标量自动包成数组）', () => {
    const data = { tags: ['甲'], author: '国务院' };
    expect(fmItems(data, 'tags')).toEqual(['甲']);
    expect(fmItems(data, 'author')).toEqual(['国务院']);
    expect(fmItems(data, 'missing')).toEqual([]);
  });

  it('键名大小写不敏感（LinkTitle / linktitle）', () => {
    const data = { LinkTitle: '法规名' };
    expect(fmValue(data, 'linktitle')).toBe('法规名');
    expect(fmText(data, 'LINKTITLE')).toBe('法规名');
  });
});
