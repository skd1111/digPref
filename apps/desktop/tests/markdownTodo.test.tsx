/**
 * markdownTodo.test.tsx —— Markdown 任务列表渲染（2026-08-10）。
 *
 * aicss 风格升级：`- [ ] / - [x]` 不再降级为纯文本，
 * 渲染为带 n/N 进度计数的 To-do 卡片（AiTodoList）。
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Markdown } from "@/components/chat/Markdown";

describe("Markdown 任务列表 → aicss To-do 卡片", () => {
  it("任务列表渲染进度计数与勾选项", () => {
    const text = ["- [x] 搭建组件骨架", "- [ ] 接入验收流程", "- [x] 写测试"].join(
      "\n",
    );
    const { container } = render(<Markdown text={text} />);
    // 进度计数 2/3
    expect(container.textContent).toContain("2/3");
    // 三项都在；已完成项带删除线
    expect(container.textContent).toContain("搭建组件骨架");
    expect(container.textContent).toContain("接入验收流程");
    const striked = Array.from(container.querySelectorAll("span")).filter(
      (el) => el.style.textDecoration === "line-through",
    );
    expect(striked.length).toBe(2);
    // 渲染为 To-do 卡片（带标题）而非普通列表
    expect(container.textContent).toContain("任务清单");
  });

  it("混合普通列表时不渲染为 To-do 卡片", () => {
    const text = ["- [x] 勾选项", "- 普通项"].join("\n");
    const { container } = render(<Markdown text={text} />);
    expect(container.textContent).not.toContain("0/");
    expect(container.querySelectorAll("ul li").length).toBe(2);
  });

  it("全部完成时显示 n/n", () => {
    const text = ["- [x] A", "- [x] B"].join("\n");
    const { container } = render(<Markdown text={text} />);
    expect(container.textContent).toContain("2/2");
  });
});
