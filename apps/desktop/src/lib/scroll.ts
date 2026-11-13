/**
 * 滚动定位工具（2026-11-06）。
 *
 * 为什么不用 scrollIntoView：它会顺带滚动**所有**可滚动祖先，包括 overflow:hidden
 * 的裁剪容器（浏览器允许对 hidden 做编程滚动）。审核模式外层是「页签行 + h-full
 * 内容区」，内容一旦溢出，scrollIntoView 会把顶部页签行（审批工作台 / 文档审核 /
 * 知识库）整行卷出可视区，用户看到的就是「点原文链接后页签不见了」。
 *
 * 因此凡是在固定面板内定位（原文块 / 风险卡），都只滚该面板自己的滚动容器。
 */

/** 在指定容器内把目标滚动到居中；容器为空时回退 scrollIntoView */
export function scrollIntoContainer(
  el: HTMLElement,
  container: HTMLElement | null,
): void {
  if (!container) {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const elRect = el.getBoundingClientRect();
  const cRect = container.getBoundingClientRect();
  const delta = elRect.top - cRect.top - (cRect.height - elRect.height) / 2;
  // jsdom / 老内核无 scrollBy：退化为直接改 scrollTop（行为一致，不掉链子）
  if (typeof container.scrollBy === "function") {
    container.scrollBy({ top: delta, behavior: "smooth" });
  } else {
    container.scrollTop += delta;
  }
}

/** 按 data-* 选择器找到目标所在的滚动容器后居中滚动 */
export function scrollWithin(el: HTMLElement, containerSelector: string): void {
  scrollIntoContainer(el, el.closest<HTMLElement>(containerSelector));
}
