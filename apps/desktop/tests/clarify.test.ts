/**
 * lib/clarify.ts —— 选项式追问解析测试。
 */
import { describe, expect, it } from 'vitest';
import { parseClarifyBlock, stripClarifyBlock } from '@/lib/clarify';

const BLOCK = [
  '分析完了，有两点需要确认。',
  '',
  '```clarify',
  '[',
  '  {',
  '    "question": "新字段是否必填？",',
  '    "options": [',
  '      {"text": "必填", "reason": "保证数据完整", "recommended": true},',
  '      {"text": "选填", "reason": "录入负担小", "recommended": false},',
  '      {"text": "看角色", "reason": "柜员必填、主管选填", "recommended": false}',
  '    ]',
  '  },',
  '  {',
  '    "question": "历史记录怎么处理？",',
  '    "options": [',
  '      {"text": "留空", "reason": "不折腾存量", "recommended": true},',
  '      {"text": "补录", "reason": "数据统一", "recommended": false}',
  '    ]',
  '  }',
  ']',
  '```',
].join('\n');

describe('parseClarifyBlock', () => {
  it('解析多问题 + 选项理由 + 推荐标记', () => {
    const r = parseClarifyBlock(BLOCK);
    expect(r).not.toBeNull();
    expect(r!.questions).toHaveLength(2);
    expect(r!.questions[0].question).toBe('新字段是否必填？');
    expect(r!.questions[0].options).toHaveLength(3);
    expect(r!.questions[0].options[0].recommended).toBe(true);
    expect(r!.questions[0].options[1].reason).toBe('录入负担小');
    // 正文被剥离
    expect(r!.text).toBe('分析完了，有两点需要确认。');
  });

  it('无 clarify 块返回 null', () => {
    expect(parseClarifyBlock('普通回答，没有问题。')).toBeNull();
    expect(parseClarifyBlock('')).toBeNull();
  });

  it('JSON 损坏返回 null（流式半截输出不误渲染）', () => {
    expect(parseClarifyBlock('```clarify\n[{"question": "x", "optio')).toBeNull();
  });

  it('过滤无效题目（无 question / 无有效选项）', () => {
    const bad =
      '```clarify\n[{"question": "", "options": [{"text": "a"}]}, {"options": []}]```\n';
    expect(parseClarifyBlock(bad)).toBeNull();
  });

  it('选项上限 5 个', () => {
    const opts = [] as string[];
    for (let i = 0; i < 7; i += 1) {
      opts.push('{"text": "o' + i + '", "reason": ""}');
    }
    const fence =
      '```clarify\n[{"question": "q", "options": [' + opts.join(',') + ']}]\n```';
    const r = parseClarifyBlock(fence);
    expect(r!.questions[0].options).toHaveLength(5);
  });
});

describe('stripClarifyBlock', () => {
  it('剥离围栏块保留正文', () => {
    expect(stripClarifyBlock(BLOCK)).toBe('分析完了，有两点需要确认。');
  });

  it('无块时原样返回', () => {
    expect(stripClarifyBlock('hello')).toBe('hello');
  });
});
