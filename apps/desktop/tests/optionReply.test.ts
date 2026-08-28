/**
 * lib/optionReply.ts —— 选项编号单字回复扩展测试（BUGFIX #150）。
 *
 * 真实翻车链路（2026-08-26）：助手给 **A./**B./**C. 粗体选项未成卡 → 用户手打
 * 「A」→ 模型只收到孤立字母丢上下文。发送时确定性扩展为结构化确认文本。
 */
import { describe, expect, it } from 'vitest';
import { expandOptionReply } from '@/lib/optionReply';

const BOLD_ABC = [
  '请您从下面挑一个（或自行补充），告诉我后我立刻起草：',
  '',
  '**A. 介绍 EAIDE 企业 AI IDE 本身**',
  '   - 用途示例：① 对客户宣讲的开场白',
  '',
  '**B. 介绍贵公司某个具体产品/平台**',
  '   - 请补充：产品名称、核心定位',
  '',
  '**C. 介绍某个内部项目/系统**',
  '   - 请补充：项目代号、所属业务线',
].join('\n');

describe('expandOptionReply', () => {
  it('孤立字母回复扩展为结构化确认文本（粗体选项也能解析）', () => {
    const out = expandOptionReply('A', BOLD_ABC);
    expect(out).toContain('[回答确认问题]');
    expect(out).toContain('选项 A → 介绍 EAIDE 企业 AI IDE 本身');
    expect(out).toContain('请按以上选择继续');
    // 粗体星号被清洗
    expect(out).not.toContain('**');
  });

  it('小写字母 / 带尾分隔符 / 「选」前缀都能命中同一选项', () => {
    expect(expandOptionReply('b', BOLD_ABC)).toContain('选项 B');
    expect(expandOptionReply('C.', BOLD_ABC)).toContain('介绍某个内部项目');
    expect(expandOptionReply('选A', BOLD_ABC)).toContain('介绍 EAIDE');
  });

  it('数字编号 + 引导语场景同样扩展', () => {
    const body = '请提供以下任一信息即可推进：\n1. 产品/项目名称（一句话即可）\n2. 或先出大纲';
    const out = expandOptionReply('2', body);
    expect(out).toContain('选项 2 → 或先出大纲');
  });

  it('编号不在选项列表内 → 原样返回', () => {
    expect(expandOptionReply('D', BOLD_ABC)).toBe('D');
    expect(expandOptionReply('9', BOLD_ABC)).toBe('9');
  });

  it('无引导语的普通步骤列表不扩展（防误伤）', () => {
    const steps = '操作步骤如下：\n1. 打开设置页\n2. 点击模型管理\n3. 启用云端后端';
    expect(expandOptionReply('1', steps)).toBe('1');
  });

  it('非孤立编号的完整回复不动（模型自己能关联）', () => {
    expect(expandOptionReply('A，另外要英文版', BOLD_ABC)).toBe('A，另外要英文版');
    expect(expandOptionReply('', BOLD_ABC)).toBe('');
  });

  it('上一条助手消息为空 / 无选项 → 原样返回', () => {
    expect(expandOptionReply('A', '')).toBe('A');
    expect(expandOptionReply('A', '好的，已为您生成报告。')).toBe('A');
  });

  it('clarify 围栏内的 JSON 不参与选项匹配（防 JSON 片段误命中）', () => {
    const withFence =
      '请选择风格：\nA. 简洁实用风\nB. 商务高级风\n\n```clarify\n[{"question": "1. x"}]\n```';
    const out = expandOptionReply('A', withFence);
    expect(out).toContain('选项 A → 简洁实用风');
  });
});
