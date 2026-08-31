/**
 * 文件路径可交互化回归（2026-08-26）。
 *
 * 用户要求：助手输出的文件位置「点击直接打开」，右键支持「在文件管理器打开」。
 * 覆盖：
 *   1. Markdown 纯文本中的绝对路径渲染为 FilePathChip（📄 + 文件名）；
 *   2. 行内代码整体是路径 → 同样是可点击胶囊；普通代码不受影响；
 *   3. 点击 → ipc.openWithDefault；右键 → 菜单含「在资源管理器中打开」并可触发
 *      ipc.revealInExplorer；
 *   4. Windows 盘符 / 正反斜杠变体均可识别；尾部标点不误吞。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Markdown } from '@/components/chat/Markdown';
import { isFilePath } from '@/components/chat/FilePathChip';

const { openWithDefault, revealInExplorer, readTextFile } = vi.hoisted(() => ({
  openWithDefault: vi.fn().mockResolvedValue('ok'),
  revealInExplorer: vi.fn().mockResolvedValue('ok'),
  readTextFile: vi.fn().mockResolvedValue(''),
}));

vi.mock('@/ipc/invoke', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/ipc/invoke')>();
  return {
    ...actual,
    ipc: {
      ...actual.ipc,
      openWithDefault,
      revealInExplorer,
      readTextFile,
    },
  };
});

beforeEach(() => {
  openWithDefault.mockClear();
  revealInExplorer.mockClear();
});

describe('路径识别', () => {
  it('Windows 盘符路径（反斜杠/正斜杠）识别为文件路径', () => {
    expect(isFilePath('C:\\Users\\79834\\eaide_intro.pptx')).toBe(true);
    expect(isFilePath('D:/workspace/tasks/a.pptx')).toBe(true);
  });

  it('普通文本与 URL 不误判', () => {
    expect(isFilePath('你好世界')).toBe(false);
    expect(isFilePath('https://example.com/a')).toBe(false);
    expect(isFilePath('随便一个句子')).toBe(false);
  });

  it('目录名含空格的路径整体识别（#170：默认工作空间路径带空格）', () => {
    expect(
      isFilePath('C:\\Users\\79834\\AppData\\Local\\Enterprise AI IDE\\workspace\\a.pptx'),
    ).toBe(true);
  });
});

describe('Markdown 渲染路径胶囊', () => {
  it('纯文本中的路径渲染为可点击胶囊（显示文件名）', () => {
    render(<Markdown text={'PPT 已生成：C:\\Users\\79834\\workspace\\eaide_intro.pptx，请查收'} />);
    expect(screen.getByText('eaide_intro.pptx')).toBeTruthy();
  });

  it('行内代码整体是路径 → 可点击胶囊而非普通 code', () => {
    const { container } = render(<Markdown text={'文件在 `D:\\out\\报告.docx`' } />);
    expect(screen.getByText('报告.docx')).toBeTruthy();
    expect(container.querySelector('code.md-ic')).toBeNull();
  });

  it('普通行内代码不受影响', () => {
    const { container } = render(<Markdown text={'执行 `npm install` 安装依赖'} />);
    expect(container.querySelector('code.md-ic')?.textContent).toBe('npm install');
  });

  it('尾部标点不吞进路径（句号留在外面）', () => {
    render(<Markdown text={'已保存到 C:\\tmp\\a.txt。'} />);
    expect(screen.getByText('a.txt')).toBeTruthy();
  });

  it('目录名含空格的路径完整成胶囊（#170：跨空格吸收到扩展名锚）', () => {
    render(
      <Markdown
        text={
          '产物 · C:\\Users\\79834\\AppData\\Local\\Enterprise AI IDE\\workspace\\docs\\EAIDE_Intro.pptx'
        }
      />,
    );
    expect(screen.getByText('EAIDE_Intro.pptx')).toBeTruthy();
  });

  it('路径后跟空格散文不误吞（扩展名锚之外的文本保持原样）', () => {
    render(<Markdown text={'PPT 已生成：C:\\out\\b.pptx 请查收'} />);
    expect(screen.getByText('b.pptx')).toBeTruthy();
    expect(screen.getByText(/请查收/)).toBeTruthy();
  });
});

describe('点击与右键交互', () => {
  it('左键点击 → 用默认程序打开', async () => {
    render(<Markdown text={'C:\\Users\\x\\slides.pptx'} />);
    fireEvent.click(screen.getByText('slides.pptx'));
    await new Promise((r) => setTimeout(r, 0));
    expect(openWithDefault).toHaveBeenCalledWith('C:\\Users\\x\\slides.pptx');
  });

  it('带空格目录路径点击 → 传完整路径给默认程序（#170）', async () => {
    const full =
      'C:\\Users\\79834\\AppData\\Local\\Enterprise AI IDE\\workspace\\docs\\EAIDE_Intro.pptx';
    render(<Markdown text={`产物 · ${full}`} />);
    fireEvent.click(screen.getByText('EAIDE_Intro.pptx'));
    await new Promise((r) => setTimeout(r, 0));
    expect(openWithDefault).toHaveBeenCalledWith(full);
  });

  it('右键 → 弹出菜单并可触发资源管理器定位', async () => {
    render(<Markdown text={'C:\\Users\\x\\slides.pptx'} />);
    fireEvent.contextMenu(screen.getByText('slides.pptx'));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText('在资源管理器中打开')).toBeTruthy();
    expect(screen.getByText('复制路径')).toBeTruthy();
    fireEvent.click(screen.getByText('在资源管理器中打开'));
    await new Promise((r) => setTimeout(r, 0));
    expect(revealInExplorer).toHaveBeenCalledWith('C:\\Users\\x\\slides.pptx');
  });

  it('文本类路径右键菜单包含「在编辑器中打开」', async () => {
    render(<Markdown text={'见 D:\\proj\\notes.md'} />);
    fireEvent.contextMenu(screen.getByText('notes.md'));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText('在编辑器中打开')).toBeTruthy();
  });
});
