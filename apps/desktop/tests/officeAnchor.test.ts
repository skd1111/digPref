/**
 * officeAnchor —— Office 版式预览定位注入件单测（2026-11-06）。
 *
 * 覆盖：
 *   - 无有效锚点时不注入任何内容
 *   - 注入片段含样式 + 脚本 + 锚点数据，且 `</script` 被转义（防提前闭合）
 *   - 表格单元格（列）优先于正文段落匹配，滚动同时带 inline:'center'（横向可见）
 *   - injectAnchors 优先插到 </body> 前
 */
import { describe, expect, it } from "vitest";
import { JSDOM } from "jsdom";
import { buildAnchorInjection, injectAnchors, OFFICE_RENDERABLE_EXTS } from "@/components/office/officeAnchor";

describe("buildAnchorInjection", () => {
  it("无锚点或锚点文本为空时不注入", () => {
    expect(buildAnchorInjection([])).toBe("");
    expect(buildAnchorInjection([{ id: "f1", text: "   " }])).toBe("");
  });

  it("生成样式 + 脚本 + 锚点数据", () => {
    const html = buildAnchorInjection([{ id: "f1", text: "违约金上限为合同金额的 100%。", level: "high" }]);
    expect(html).toContain("<style>");
    expect(html).toContain("<script>");
    expect(html).toContain("eaide-hl");
    expect(html).toContain("f1");
    expect(html).toContain("违约金上限为合同金额的 100%。");
  });

  it("锚点文本含 </script 时被转义（防提前闭合脚本块）", () => {
    const html = buildAnchorInjection([{ id: "f1", text: "</script><img onerror=alert(1)>" }]);
    expect(html).not.toContain("</script><img");
    expect(html).toContain("<\\/script>");
  });

  it("表格单元格（列）优先匹配，且滚动带 inline:center（宽表格也能看到该列）", () => {
    const html = buildAnchorInjection([{ id: "f1", text: "甲方应当按期支付全部款项" }]);
    // 先找列（td/th），再找正文段落
    expect(html.indexOf("'td,th'")).toBeGreaterThan(-1);
    expect(html.indexOf("tryMatch(cells, usedCell)")).toBeGreaterThan(-1);
    expect(html.indexOf("tryMatch(paras, usedPara)")).toBeGreaterThan(-1);
    // 命中单元格 → 整行淡背景 + 单元格强高亮
    expect(html).toContain("eaide-hl-row");
    expect(html).toContain("data-eaide-level");
    // 滚动：纵向居中 + 横向居中
    expect(html).toContain("block: 'center', inline: 'center'");
  });

  it("命中结果用 postMessage 回传给父页面", () => {
    const html = buildAnchorInjection([{ id: "f1", text: "违约责任条款" }]);
    expect(html).toContain("eaide-anchor-result");
    expect(html).toContain("parent.postMessage");
  });
});

describe("injectAnchors", () => {
  it("无锚点时原样返回", () => {
    expect(injectAnchors("<html><body>x</body></html>", [])).toBe("<html><body>x</body></html>");
  });

  it("注入片段插到 </body> 之前", () => {
    const out = injectAnchors("<html><body><p>合同</p></body></html>", [
      { id: "f1", text: "合同条款内容" },
    ]);
    expect(out.indexOf("<script>")).toBeGreaterThan(out.indexOf("<p>合同</p>"));
    expect(out.indexOf("<script>")).toBeLessThan(out.indexOf("</body>"));
  });

  it("无 </body> 时直接追加到末尾", () => {
    const out = injectAnchors("<p>片段</p>", [{ id: "f1", text: "片段内容" }]);
    expect(out.startsWith("<p>片段</p>")).toBe(true);
    expect(out).toContain("<script>");
  });
});

describe("OFFICE_RENDERABLE_EXTS", () => {
  it("仅 docx / xlsx / pptx 有内置版式渲染器", () => {
    expect(OFFICE_RENDERABLE_EXTS.has("docx")).toBe(true);
    expect(OFFICE_RENDERABLE_EXTS.has("xlsx")).toBe(true);
    expect(OFFICE_RENDERABLE_EXTS.has("pptx")).toBe(true);
    expect(OFFICE_RENDERABLE_EXTS.has("pdf")).toBe(false);
    expect(OFFICE_RENDERABLE_EXTS.has("doc")).toBe(false);
  });
});

/** 真实渲染页结构回归：注入脚本在 OfficeCLI 产物（<p data-path> + 表格）内定位到「那一列」并高亮。 */
describe("注入脚本在渲染页内的定位行为", () => {
  const RENDERED = `<html><body>
    <p data-path="/body/p[1]" style="font-size:11pt">借款合同</p>
    <table>
      <tr><td>甲方</td><td>乙方</td></tr>
      <tr><td>某公司</td><td>违约金上限为合同金额的 100%。</td></tr>
    </table>
  </body></html>`;

  it("命中表格单元格（对应列），并给所在行加淡背景", async () => {
    const out = injectAnchors(RENDERED, [
      { id: "f1", text: "违约金上限为合同金额的 100%。", level: "high" },
    ]);
    const dom = new JSDOM(out, { runScripts: "dangerously", pretendToBeVisual: true });
    await new Promise((r) => setTimeout(r, 300));
    const doc = dom.window.document;
    const hl = doc.querySelector(".eaide-hl");
    expect(hl).not.toBeNull();
    // 命中的是单元格（列），而不是整行或正文段
    expect(hl?.tagName).toBe("TD");
    expect(hl?.textContent).toContain("违约金上限");
    expect(hl?.getAttribute("data-eaide-level")).toBe("high");
    expect(hl?.getAttribute("data-eaide-hl")).toBe("f1");
    // 所在行有淡背景（明确哪一行）
    expect(hl?.closest("tr")?.classList.contains("eaide-hl-row")).toBe(true);
    dom.window.close();
  });

  it("正文段落也能命中（非表格场景）", async () => {
    const out = injectAnchors(
      `<html><body><p data-path="/body/p[2]">保密义务自协议签署之日起生效</p></body></html>`,
      [{ id: "f2", text: "保密义务自协议签署之日起生效", level: "medium" }],
    );
    const dom = new JSDOM(out, { runScripts: "dangerously", pretendToBeVisual: true });
    await new Promise((r) => setTimeout(r, 300));
    const hl = dom.window.document.querySelector(".eaide-hl");
    expect(hl?.tagName).toBe("P");
    expect(hl?.getAttribute("data-eaide-level")).toBe("medium");
    dom.window.close();
  });
});
