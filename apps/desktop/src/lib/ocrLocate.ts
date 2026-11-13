/**
 * ocrLocate —— 凭证影像要素定位（2026-11-09 凭证预览）。
 *
 * 端侧 RapidOCR 落库的是一行行 `{text, bbox[[x,y]×4], confidence}`；
 * 面板要的是「点录入的字段名 → 这张图放大到哪、哪个框亮」。
 * 这里把这件事做成纯函数（不碰 DOM），影像层只负责渲染结果：
 *   1. normalizeOcrText：全半角 + OCR 混淆字符归一，值与行文本才能对上；
 *   2. groupOcrLines：证件/票据常见把同一个值拆进相邻几块，先按行合并再匹配；
 *   3. locateValue：命中即返回外接矩形（并集）+ 命中的原文；
 *   4. focusTransform / applyWheelZoom：把矩形居中放大、滚轮以光标为锚点缩放。
 */
import type { OcrBlock } from "@/types/ops";

/** 影像上的外接矩形（像素坐标，与图片原始尺寸同系） */
export interface OcrRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 视口变换：容器内层元素 `translate(tx,ty) scale(scale)`，transformOrigin 左上角 */
export interface ViewTransform {
  scale: number;
  tx: number;
  ty: number;
}

export interface LocateHit {
  /** 命中区域（可能是多块并集） */
  rect: OcrRect;
  /** 命中的原始行文本（面板显示「识别原文」用） */
  matched: string[];
  /** 命中块里的最低置信度（保守取值，低于 0.6 提示人工核对） */
  confidence: number;
  /** 命中方式：exact 原文包含 / normalized 归一后包含 */
  how: "exact" | "normalized";
}

// 全角 ASCII（含标点）→ 半角：U+FF01 与 U+0021 固定相差 0xFEE0
function toHalfWidth(s: string): string {
  return s.replace(/[\uFF01-\uFF5E]/g, (ch) =>
    String.fromCharCode(ch.charCodeAt(0) - 0xfee0),
  );
}

// OCR 常见混淆：这些替换只用于「比较」，不改变展示原文
const CONFUSION: ReadonlyArray<readonly [string, string]> = [
  ["O", "0"],
  ["o", "0"],
  ["D", "0"],
  ["Q", "0"],
  ["I", "1"],
  ["l", "1"],
  ["i", "1"],
  ["|", "1"],
  ["!", "1"],
  ["S", "5"],
  ["s", "5"],
  ["B", "8"],
  ["Z", "2"],
  ["z", "2"],
  ["G", "6"],
  ["g", "9"],
];

/**
 * 比较用归一：全角转半角 → 去掉空白与常见分隔标点 → 大写 → 混淆字符对齐。
 * 长度阈值与匹配策略交给调用方，这里只保证同一算法可复现。
 */
export function normalizeOcrText(input: string): string {
  let s = toHalfWidth(String(input ?? ""));
  s = s.replace(
    /[\s_\-–—/\\.,:;、。，：；·（）()[\]{}【】<>《》"'`~*#&+%=^$@!？?]+/g,
    "",
  );
  s = s.toUpperCase();
  for (const [from, to] of CONFUSION) {
    s = s.split(from.toUpperCase()).join(to);
  }
  return s;
}

/** 四点框 → 外接矩形；点数不足或坐标非法返回 null */
export function blockRect(bbox: number[][] | undefined | null): OcrRect | null {
  if (!Array.isArray(bbox) || bbox.length < 3) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const pt of bbox) {
    if (!Array.isArray(pt) || pt.length < 2) return null;
    const x = Number(pt[0]);
    const y = Number(pt[1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  if (!(maxX > minX && maxY > minY)) return null;
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}

/** 矩形并集（空集返回 null） */
export function unionRects(rects: OcrRect[]): OcrRect | null {
  if (rects.length === 0) return null;
  let x1 = Infinity;
  let y1 = Infinity;
  let x2 = -Infinity;
  let y2 = -Infinity;
  for (const r of rects) {
    x1 = Math.min(x1, r.x);
    y1 = Math.min(y1, r.y);
    x2 = Math.max(x2, r.x + r.w);
    y2 = Math.max(y2, r.y + r.h);
  }
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

/** 有效块（坐标可解析）+ 归一文本，供行分组与匹配复用 */
interface PreparedBlock {
  rect: OcrRect;
  text: string;
  norm: string;
  confidence: number;
  centerY: number;
  height: number;
  x: number;
}

function prepareBlocks(blocks: OcrBlock[]): PreparedBlock[] {
  const out: PreparedBlock[] = [];
  for (const b of blocks ?? []) {
    const rect = blockRect(b?.bbox);
    if (!rect) continue;
    const text = String(b.text ?? "").trim();
    if (!text) continue;
    out.push({
      rect,
      text,
      norm: normalizeOcrText(text),
      confidence: Number.isFinite(b.confidence) ? b.confidence : 0,
      centerY: rect.y + rect.h / 2,
      height: rect.h,
      x: rect.x,
    });
  }
  return out;
}

/**
 * 按行分组：中心 y 足够接近（< 0.7 倍行高）视为同一行，行内按 x 排序。
 * 证件/票据常把「企业名称：某某公司」切成 2-3 块，合并后才能整串匹配。
 */
export function groupOcrLines(blocks: OcrBlock[]): PreparedBlock[][] {
  const prepared = prepareBlocks(blocks).sort((a, b) => a.centerY - b.centerY);
  if (prepared.length === 0) return [];
  const avgH = prepared.reduce((s, p) => s + p.height, 0) / prepared.length;
  const tolerance = Math.max(avgH * 0.7, 1);
  const lines: PreparedBlock[][] = [];
  let bucket: PreparedBlock[] = [];
  let anchor = prepared[0].centerY;
  for (const p of prepared) {
    if (bucket.length > 0 && Math.abs(p.centerY - anchor) > tolerance) {
      lines.push(bucket);
      bucket = [];
      anchor = p.centerY;
    }
    bucket.push(p);
  }
  if (bucket.length > 0) lines.push(bucket);
  return lines.map((line) => line.sort((a, b) => a.x - b.x));
}

/** 一组块的命中分数：命中原文越长越可信（避免短行误命中） */
function scoreMatch(needle: string, hay: string): number {
  if (!needle || !hay) return 0;
  if (hay.includes(needle))
    return needle.length / Math.max(hay.length, needle.length);
  if (needle.includes(hay) && hay.length >= 4)
    return (hay.length / Math.max(needle.length, hay.length)) * 0.6;
  return 0;
}

/** 命中候选（取分数最高的一条） */
interface LocateCandidate {
  rect: OcrRect;
  matched: string[];
  confidence: number;
  score: number;
  how: "exact" | "normalized";
}

function pickBest(list: LocateCandidate[]): LocateHit | null {
  if (list.length === 0) return null;
  const best = list.reduce((a, b) => (b.score > a.score ? b : a));
  return {
    rect: best.rect,
    matched: best.matched,
    confidence: best.confidence,
    how: best.how,
  };
}

/**
 * 值文本 → 影像上的命中区域。
 *
 * 先按单块找（精确原文），再按合并行找（跨块的值），都走归一比较；
 * 命中的值太短（< 2 字符，如单个「无」）直接放弃 —— 误高亮比不高亮更糟。
 */
export function locateValue(
  value: string,
  blocks: OcrBlock[],
): LocateHit | null {
  const raw = String(value ?? "").trim();
  const norm = normalizeOcrText(raw);
  if (norm.length < 2) return null;
  const prepared = prepareBlocks(blocks);
  if (prepared.length === 0) return null;

  // 1) 单块精确原文优先（不改字符就能对上，说明识别质量高）
  const exact: LocateCandidate[] = [];
  for (const p of prepared) {
    if (raw.length >= 2 && p.text.includes(raw)) {
      exact.push({
        rect: p.rect,
        matched: [p.text],
        confidence: p.confidence,
        score: 1,
        how: "exact",
      });
    }
  }
  if (exact.length > 0) return pickBest(exact);

  // 2) 归一后比较：单块 + 合并行（跨块值）
  const fuzzy: LocateCandidate[] = [];
  for (const p of prepared) {
    const s = scoreMatch(norm, p.norm);
    if (s > 0) {
      fuzzy.push({
        rect: p.rect,
        matched: [p.text],
        confidence: p.confidence,
        score: s,
        how: "normalized",
      });
    }
  }
  for (const line of groupOcrLines(blocks)) {
    if (line.length < 2) continue;
    const text = line.map((p) => p.text).join("");
    const lineNorm = line.map((p) => p.norm).join("");
    const s = scoreMatch(norm, lineNorm);
    const rect = unionRects(line.map((p) => p.rect));
    if (!rect || s <= 0) continue;
    fuzzy.push({
      rect,
      matched: [text],
      confidence: Math.min(...line.map((p) => p.confidence)),
      score: s,
      how: "normalized",
    });
  }
  return pickBest(fuzzy);
}

/** 适应窗口（整图可见）的缩放比 */
export function fitScale(
  imgW: number,
  imgH: number,
  viewW: number,
  viewH: number,
): number {
  if (imgW <= 0 || imgH <= 0 || viewW <= 0 || viewH <= 0) return 1;
  const s = Math.min(viewW / imgW, viewH / imgH);
  return Number.isFinite(s) && s > 0 ? s : 1;
}

/**
 * 把命中区域居中并放大到「约占视口一半」，夹在 [fit, maxScale] 之间。
 * 返回内层元素的 transform 参数（transformOrigin: '0 0'）。
 */
export function focusTransform(
  rect: OcrRect,
  imgW: number,
  imgH: number,
  viewW: number,
  viewH: number,
  opts?: { maxScale?: number; fillRatio?: number },
): ViewTransform {
  const maxScale = opts?.maxScale ?? 6;
  const fillRatio = opts?.fillRatio ?? 0.5;
  const fit = fitScale(imgW, imgH, viewW, viewH);
  const wantW = rect.w > 0 ? (viewW * fillRatio) / rect.w : fit;
  const wantH = rect.h > 0 ? (viewH * fillRatio) / rect.h : fit;
  const scale = Math.max(fit, Math.min(maxScale, Math.max(wantW, wantH)));
  const cx = rect.x + rect.w / 2;
  const cy = rect.y + rect.h / 2;
  return {
    scale,
    tx: viewW / 2 - scale * cx,
    ty: viewH / 2 - scale * cy,
  };
}

/**
 * 以某个视口内锚点（通常是光标）为中心缩放，保持锚点下的图像不漂移。
 */
export function applyWheelZoom(
  current: ViewTransform,
  factor: number,
  anchorX: number,
  anchorY: number,
  minScale = 0.2,
  maxScale = 12,
): ViewTransform {
  const s0 = current.scale > 0 ? current.scale : 1;
  const scale = Math.max(minScale, Math.min(maxScale, s0 * factor));
  const k = scale / s0;
  return {
    scale,
    tx: anchorX - (anchorX - current.tx) * k,
    ty: anchorY - (anchorY - current.ty) * k,
  };
}

/** 矩形 → SVG rect 属性（四点框退化为外接框，保持渲染简单） */
export function rectToAttrs(rect: OcrRect): {
  x: number;
  y: number;
  width: number;
  height: number;
} {
  return { x: rect.x, y: rect.y, width: rect.w, height: rect.h };
}
