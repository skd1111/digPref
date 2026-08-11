/**
 * markdownCodeBlock.test.tsx —— 围栏代码块 aicss 风格测试（2026-08-10）。
 *
 * 覆盖：语言头展示 / 行号行渲染 / pre 纯文本兼容（行号走 CSS 计数器，
 * 不进 textContent）。
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Markdown } from "@/components/chat/Markdown";

describe("Markdown 围栏代码块（aicss 风格）", () => {
  it("围栏语言显示在头部", () => {
    const text = ["```sql", "SELECT 1;", "```"].join("\n");
    const { container } = render(<Markdown text={text} />);
    const lang = container.querySelector(".md-code-lang");
    expect(lang?.textContent).toContain("sql");
  });

  it("多行代码按行渲染且 pre 文本不含行号", () => {
    const text = ["```python", "a = 1", "b = 2", "```"].join("\n");
    const { container } = render(<Markdown text={text} />);
    const lines = container.querySelectorAll(".md-code pre .md-line");
    expect(lines.length).toBe(2);
    // 行号由 CSS ::before 计数器生成，不进 textContent
    const pre = container.querySelector(".md-code pre");
    expect(pre?.textContent).toBe("a = 1\nb = 2");
  });

  it("无语言围栏降级为 text", () => {
    const text = ["```", "hello", "```"].join("\n");
    const { container } = render(<Markdown text={text} />);
    expect(container.querySelector(".md-code-lang")?.textContent).toContain(
      "text",
    );
  });
});
