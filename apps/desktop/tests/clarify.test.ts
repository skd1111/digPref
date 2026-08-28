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

  it('围栏内 JSON 损坏也照样剥离，原始 JSON 不裸露给用户（#149）', () => {
    const broken = '结论如下\n\n```clarify\n[{"question": "x", "options": [BROKEN```';
    // 围栏闭合但 JSON 非法：解析不出卡片，正文仍只留可读部分（不含 JSON）
    expect(parseClarifyBlock(broken)).toBeNull();
    expect(stripClarifyBlock(broken)).toBe('结论如下');
  });
});

describe('多选题卡片（multi 标记，BUGFIX #149）', () => {
  const MULTI_BLOCK = [
    '您希望侧重哪些内容？',
    '',
    '```clarify',
    '[{"question": "侧重哪些内容？（可多选）", "multi": true, "options": [',
    '{"text": "基础身份", "reason": "", "recommended": true},',
    '{"text": "核心能力", "reason": "", "recommended": false}]}]',
    '```',
  ].join('\n');

  it('解析 multi=true（后端题干含「多选」字样时下发）', () => {
    const r = parseClarifyBlock(MULTI_BLOCK);
    expect(r).not.toBeNull();
    expect(r!.questions[0].multi).toBe(true);
    expect(r!.questions[0].options).toHaveLength(2);
  });

  it('旧数据无 multi 字段 → 按单选渲染（向后兼容）', () => {
    const r = parseClarifyBlock(BLOCK);
    expect(r!.questions[0].multi).toBe(false);
    expect(r!.questions[1].multi).toBe(false);
  });

  it('multi 非布尔值（模型超发脏字段）不炸解析，归一为 false', () => {
    const dirty = '```clarify\n[{"question": "q", "multi": "yes", "options": [{"text": "a"}, {"text": "b"}]}]```';
    const r = parseClarifyBlock(dirty);
    expect(r).not.toBeNull();
    expect(r!.questions[0].multi).toBe(false);
  });
});

describe('确认卡（参数摘要 + 确认/修改，2026-08-14）', () => {
  // 与后端 responder._confirmation_body 的真实输出对齐（模型接入确认场景）
  const CONFIRM_BLOCK = [
    '将按以下参数接入 DeepSeek-RD-Llama-70B-Int8：endpoint=http://172.1.0.134:8000，api_key 默认空。',
    '',
    '（未确认前不会执行任何操作。）',
    '',
    '```clarify',
    '[',
    '  {',
    '    "question": "确认按上述参数执行？",',
    '    "options": [',
    '      {"text": "确认执行", "reason": "参数摘要核对无误，继续执行", "recommended": true},',
    '      {"text": "修改参数", "reason": "选这项并在下方直接告诉我要改什么", "recommended": false}',
    '    ]',
    '  }',
    ']',
    '```',
  ].join('\n');

  it('单问题 + 确认/修改两选项 + 恰好一个推荐项', () => {
    const r = parseClarifyBlock(CONFIRM_BLOCK);
    expect(r).not.toBeNull();
    expect(r!.questions).toHaveLength(1);
    expect(r!.questions[0].question).toBe('确认按上述参数执行？');
    expect(r!.questions[0].options.map((o) => o.text)).toEqual(['确认执行', '修改参数']);
    expect(r!.questions[0].options.filter((o) => o.recommended)).toHaveLength(1);
  });

  it('正文保留参数摘要，剥离围栏块', () => {
    const r = parseClarifyBlock(CONFIRM_BLOCK);
    expect(r!.text).toContain('将按以下参数接入 DeepSeek-RD-Llama-70B-Int8');
    expect(r!.text).toContain('未确认前不会执行');
    expect(r!.text).not.toContain('```clarify');
  });
});
