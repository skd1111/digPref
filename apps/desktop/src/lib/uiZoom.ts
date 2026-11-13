/**
 * uiZoom —— 界面缩放（2026-11-09）。
 *
 * 背景：界面按 VSCode 12px 密度设计，但组件里散着约 200 处 px 硬编码
 * （text-[10px] / .btn 固定 px），只改某个字号 token 会「一部分变大、一部分没变」。
 * 因此缩放统一走 WebView 原生 zoom（@tauri-apps/api/webview 的 setZoom）：
 * 视口 CSS px 同步变小，文字 / 按钮 / 间距 / fixed 浮层整体等比放大，
 * 不需要逐处改字号。非 Tauri 环境（浏览器预览 / vitest）降级为 CSS zoom。
 *
 * 缩放值连续可调（80%–200%），菜单 Zoom In/Out 每次 10%，设置页滑块 5%。
 */

/** 缩放范围与步长（2026-11-09 改为连续可调）
 *  范围：80%–200%；菜单 Zoom In/Out 每档 10%，设置页滑块 5% */
export const UI_ZOOM_MIN = 0.8;
export const UI_ZOOM_MAX = 2;
export const UI_ZOOM_STEP = 0.1;

/** 默认 110%：用户反馈 12px 基准偏小，装完即有可感知的放大 */
export const UI_ZOOM_DEFAULT = 1.1;

const round2 = (zoom: number): number => Math.round(zoom * 100) / 100;

/** 夹到合法区间（两位精度，防 localStorage 脏值 / 滑块浮点尾数） */
export function clampUiZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return UI_ZOOM_DEFAULT;
  return Math.min(UI_ZOOM_MAX, Math.max(UI_ZOOM_MIN, round2(zoom)));
}

/** 放大一档（+10%，到顶档则不动） */
export function zoomInFrom(zoom: number): number {
  return clampUiZoom(zoom + UI_ZOOM_STEP);
}

/** 缩小一档（-10%，到底档则不动） */
export function zoomOutFrom(zoom: number): number {
  return clampUiZoom(zoom - UI_ZOOM_STEP);
}

/** 是否已到最小/最大档（菜单置灰用） */
export function isZoomAtMin(zoom: number): boolean {
  return zoom <= UI_ZOOM_MIN + 1e-6;
}

export function isZoomAtMax(zoom: number): boolean {
  return zoom >= UI_ZOOM_MAX - 1e-6;
}

/** 1.1 → "110%" */
export function zoomPercent(zoom: number): string {
  return `${Math.round(zoom * 100)}%`;
}

/**
 * 把缩放到当前 WebView。
 * Tauri 环境走原生 zoom（视口自适应，fixed/滚动都正常）；
 * 单测 / 浏览器预览没有 __TAURI_INTERNALS__，降级为 CSS zoom 只为可测。
 */
export async function applyUiZoom(zoom: number): Promise<void> {
  const scale = clampUiZoom(zoom);
  try {
    const { getCurrentWebview } = await import('@tauri-apps/api/webview');
    await getCurrentWebview().setZoom(scale);
  } catch {
    document.documentElement.style.setProperty('zoom', String(scale));
  }
}
