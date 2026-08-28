/**
 * ClarifyCard 多选题交互测试（BUGFIX #149）：
 *   - 多选题（multi）：复选框连选多项，回发文本以「、」连接
 *   - 单选题行为不变：替换选中、自动跳下一题页签
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { ClarifyQuestion } from '@/lib/clarify';
import { ClarifyCard } from '@/components/chat/ClarifyCard';

const MULTI_Q: ClarifyQuestion = {
  question: '侧重哪些内容？（可多选）',
  multi: true,
  options: [
    { text: '基础身份', reason: '', recommended: true },
    { text: '核心能力', reason: '', recommended: false },
    { text: '使用场景', reason: '', recommended: false },
  ],
};

const SINGLE_Q1: ClarifyQuestion = {
  question: '风格选哪个？',
  options: [
    { text: '简洁实用', reason: '', recommended: true },
    { text: '商务高级', reason: '', recommended: false },
  ],
};

const SINGLE_Q2: ClarifyQuestion = {
  question: '页数呢？',
  options: [
    { text: '5 页', reason: '', recommended: false },
    { text: '10 页', reason: '', recommended: false },
  ],
};

function optionButtons(container: HTMLElement): HTMLButtonElement[] {
  // 选项按钮 = 带 ◉/○/☑/☐ 标记的按钮（排除页签「问题 n」与「发送回答」）
  return Array.from(container.querySelectorAll('button')).filter((b) =>
    /[◉○☑☐]/.test(b.textContent ?? ''),
  );
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
  const btn = Array.from(container.querySelectorAll('button')).find((b) =>
    (b.textContent ?? '').includes(text),
  );
  if (!btn) throw new Error(`button not found: ${text}`);
  return btn;
}

describe('ClarifyCard 多选交互（#149）', () => {
  let container: HTMLDivElement;
  let root: Root;

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) container.remove();
  });

  async function render(questions: ClarifyQuestion[], onSend: (t: string) => void): Promise<void> {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(<ClarifyCard questions={questions} busy={false} onSend={onSend} />);
    });
  }

  it('多选题可连选多项，回发文本以「、」连接', async () => {
    const onSend = vi.fn();
    await render([MULTI_Q], onSend);

    const opts = optionButtons(container);
    expect(opts).toHaveLength(3);
    // 题干带「可多选」提示
    expect(container.textContent).toContain('可多选');
    // 复选框标记（非单选圆点）
    expect(container.textContent).toContain('☐');

    // 推荐项已预选（基础身份 ☑），再勾「使用场景」→ 两项并存
    await act(async () => {
      opts[2].click();
    });
    expect(container.textContent).toContain('☑');

    await act(async () => {
      findButton(container, '发送回答').click();
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0]).toContain('基础身份、使用场景');
  });

  it('多选题再点已选项 = 取消勾选', async () => {
    const onSend = vi.fn();
    await render([MULTI_Q], onSend);

    const opts = optionButtons(container);
    // 取消推荐项预选，改选「核心能力」
    await act(async () => {
      opts[0].click();
      opts[1].click();
    });
    await act(async () => {
      findButton(container, '发送回答').click();
    });
    expect(onSend.mock.calls[0][0]).toContain('→ 核心能力');
    expect(onSend.mock.calls[0][0]).not.toContain('基础身份');
  });

  it('单选题保持原行为：替换选中 + 自动跳下一题页签', async () => {
    const onSend = vi.fn();
    await render([SINGLE_Q1, SINGLE_Q2], onSend);

    // 第一题选了非推荐项 → 自动跳到第二题（页签 2 高亮）
    const opts = optionButtons(container);
    await act(async () => {
      opts[1].click(); // 商务高级
    });
    expect(container.textContent).toContain('（2/2）');

    // 第二题无推荐项不预选 → 选「10 页」后可发送
    const opts2 = optionButtons(container);
    await act(async () => {
      opts2[1].click();
    });
    await act(async () => {
      findButton(container, '发送回答').click();
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend.mock.calls[0][0]).toContain('风格选哪个？ → 商务高级');
    expect(onSend.mock.calls[0][0]).toContain('页数呢？ → 10 页');
  });
});
