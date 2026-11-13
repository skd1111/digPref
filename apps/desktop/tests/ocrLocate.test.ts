/**
 * ocrLocate.test.ts —— 凭证影像要素定位纯函数测试（2026-11-09）。
 *
 * 覆盖「点录入字段 → 放大高亮对应区域」链路里最容易出错的两段：
 *   1) 识别文本与录入值的匹配（全半角、OCR 混淆字符、值被切成多块）；
 *   2) 变换数学（居中、缩放夹取、以光标为锚点）。
 */
import { describe, expect, it } from "vitest";
import {
  applyWheelZoom,
  blockRect,
  fitScale,
  focusTransform,
  groupOcrLines,
  locateValue,
  normalizeOcrText,
  unionRects,
} from "@/lib/ocrLocate";
import type { OcrBlock } from "@/types/ops";

/** 造一块文字框：左上角 (x,y) + 宽高 */
function block(
  text: string,
  x: number,
  y: number,
  w: number,
  h: number,
  conf = 0.9,
): OcrBlock {
  return {
    text,
    bbox: [
      [x, y],
      [x + w, y],
      [x + w, y + h],
      [x, y + h],
    ],
    confidence: conf,
  };
}

describe("normalizeOcrText", () => {
  it("全角转半角并去掉空白标点", () => {
    // B/8 属于混淆折叠（只用于比较，不改展示原文），所以 ＡＢＣ → A8C
    expect(normalizeOcrText("ＡＢＣ　１２３")).toBe("A8C123");
    expect(normalizeOcrText("91330100, MA2AB-CDX1")).toBe(
      normalizeOcrText("91330100MA2ABCDX1"),
    );
  });

  it("OCR 混淆字符对齐（O/0、I/1、S/5）", () => {
    expect(normalizeOcrText("O1S")).toBe(normalizeOcrText("015"));
  });

  it("空值与非字符串安全", () => {
    expect(normalizeOcrText("")).toBe("");
    expect(normalizeOcrText(undefined as unknown as string)).toBe("");
  });
});

describe("blockRect / unionRects", () => {
  it("四点框 → 外接矩形", () => {
    expect(blockRect(block("x", 10, 20, 100, 30).bbox)).toEqual({
      x: 10,
      y: 20,
      w: 100,
      h: 30,
    });
  });

  it("点数不足或坐标非法返回 null", () => {
    expect(
      blockRect([
        [0, 0],
        [1, 1],
      ]),
    ).toBeNull();
    expect(
      blockRect([
        [0, 0],
        [1, 1],
        [Number.NaN, 2],
        [0, 2],
      ]),
    ).toBeNull();
    expect(blockRect(undefined)).toBeNull();
  });

  it("并集覆盖全部矩形；空集 null", () => {
    const a = { x: 0, y: 0, w: 10, h: 10 };
    const b = { x: 20, y: 5, w: 10, h: 10 };
    // x 0..30，y 0..15（b 底边 5+10）
    expect(unionRects([a, b])).toEqual({ x: 0, y: 0, w: 30, h: 15 });
    expect(unionRects([])).toBeNull();
  });
});

describe("groupOcrLines", () => {
  it("中心 y 足够接近视为同一行，行内按 x 排序", () => {
    const lines = groupOcrLines([
      block("第二行", 10, 100, 60, 20),
      block("右块", 90, 22, 60, 20),
      block("左块", 10, 20, 60, 20),
    ]);
    expect(lines.length).toBe(2);
    expect(lines[0].map((p) => p.text)).toEqual(["左块", "右块"]);
    expect(lines[1][0].text).toBe("第二行");
  });

  it("空输入返回空数组", () => {
    expect(groupOcrLines([])).toEqual([]);
  });
});

describe("locateValue", () => {
  const blocks = [
    block("企业名称：某某贸易有限公司", 10, 20, 300, 24),
    block("统一社会信用代码", 10, 60, 120, 24),
    block("9133 0100", 140, 60, 80, 24),
    block("MA2ABCDX1", 230, 60, 90, 24),
    block("营业期限", 10, 100, 80, 24),
  ];

  it("单块精确包含 → exact 命中", () => {
    const hit = locateValue("某某贸易有限公司", blocks);
    expect(hit).not.toBeNull();
    expect(hit?.how).toBe("exact");
    expect(hit?.rect).toEqual({ x: 10, y: 20, w: 300, h: 24 });
  });

  it("值被切成同行多块 → 命中并集矩形（连标签一起框出）", () => {
    const hit = locateValue("91330100MA2ABCDX1", blocks);
    expect(hit).not.toBeNull();
    expect(hit?.how).toBe("normalized");
    // 同一行的「统一社会信用代码 / 9133 0100 / MA2ABCDX1」三块并集：x 10..320，y 60..84
    expect(hit?.rect.x).toBe(10);
    expect(hit?.rect.y).toBe(60);
    expect(hit?.rect.w).toBeCloseTo(310, 0);
    expect(hit?.rect.h).toBeCloseTo(24, 0);
  });

  it("空白与全角差异不影响匹配", () => {
    const spaced = [block("91330100 ＭＡ２ＡＢＣＤＸ１", 10, 60, 200, 24)];
    expect(locateValue("91330100MA2ABCDX1", spaced)).not.toBeNull();
  });

  it("低置信块命中时带出最低置信度（供面板提醒核对）", () => {
    const hit = locateValue("某某贸易有限公司", [
      block("企业名称：某某贸易有限公司", 10, 20, 300, 24, 0.42),
    ]);
    expect(hit?.confidence).toBeCloseTo(0.42, 5);
  });

  it("太短的值不定位（误高亮比不高亮更糟）", () => {
    expect(locateValue("无", blocks)).toBeNull();
    expect(locateValue("", blocks)).toBeNull();
  });

  it("找不到的值返回 null，不瞎框", () => {
    expect(locateValue("不存在的公司", blocks)).toBeNull();
  });

  it("空 blocks / 坐标全非法 → null", () => {
    expect(locateValue("某某", [])).toBeNull();
    expect(
      locateValue("某某", [{ text: "某某", bbox: [], confidence: 1 }]),
    ).toBeNull();
  });
});

describe("视图变换", () => {
  it("fitScale 让整图可见（横图按宽、竖图按高限制）", () => {
    expect(fitScale(1000, 500, 500, 500)).toBeCloseTo(0.5, 5);
    expect(fitScale(200, 800, 400, 400)).toBeCloseTo(0.5, 5);
    expect(fitScale(0, 100, 400, 400)).toBe(1);
  });

  it("focusTransform 把目标框居中并放大到不小于适应比例", () => {
    const rect = { x: 100, y: 100, w: 80, h: 40 };
    const t = focusTransform(rect, 800, 600, 600, 400);
    expect(t.scale).toBeGreaterThan(1);
    // 框中心 (140,120) 变换后应落在视口中心 (300,200)
    expect(t.tx + t.scale * 140).toBeCloseTo(300, 6);
    expect(t.ty + t.scale * 120).toBeCloseTo(200, 6);
  });

  it("focusTransform 缩放有上限（小字不放大到看不见整图）", () => {
    const t = focusTransform({ x: 0, y: 0, w: 2, h: 2 }, 2000, 2000, 800, 600, {
      maxScale: 6,
    });
    expect(t.scale).toBeLessThanOrEqual(6);
  });

  it("applyWheelZoom 保持光标下的点不漂移", () => {
    const start = { scale: 1, tx: 0, ty: 0 };
    const next = applyWheelZoom(start, 2, 100, 50);
    // 锚点 (100,50) 在缩放前后指向同一图像坐标
    expect((100 - next.tx) / next.scale).toBeCloseTo(
      (100 - start.tx) / start.scale,
      6,
    );
    expect((50 - next.ty) / next.scale).toBeCloseTo(
      (50 - start.ty) / start.scale,
      6,
    );
  });

  it("applyWheelZoom 夹在 min/max 之间", () => {
    expect(
      applyWheelZoom({ scale: 1, tx: 0, ty: 0 }, 100, 0, 0, 0.2, 12).scale,
    ).toBe(12);
    expect(
      applyWheelZoom({ scale: 1, tx: 0, ty: 0 }, 0.001, 0, 0, 0.2, 12).scale,
    ).toBe(0.2);
  });
});
