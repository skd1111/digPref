/**
 * vitest 全局 setup —— jsdom 缺失的浏览器 API 补齐。
 *
 * 渲染真实 <App /> 时会经 CodeEditorPane 拉入 monaco-editor，
 * monaco 的 clipboard 贡献在模块加载期访问 document.queryCommandSupported，
 * jsdom 未实现该方法（type 为 undefined），这里在测试加载前补齐。
 */

if (
  typeof document !== 'undefined' &&
  typeof (document as Document & { queryCommandSupported?: unknown }).queryCommandSupported !==
    'function'
) {
  (document as Document & { queryCommandSupported: (cmd: string) => boolean }).queryCommandSupported =
    () => false;
}

if (typeof window !== 'undefined') {
  // React 18.3：显式声明 act 环境，消除 "not configured to support act(...)" 警告
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

  if (typeof window.matchMedia !== 'function') {
    (window as Window & { matchMedia: (q: string) => MediaQueryList }).matchMedia = (query) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList;
  }

  if (typeof (window as Window & { ResizeObserver?: unknown }).ResizeObserver !== 'function') {
    (window as Window & { ResizeObserver: new () => ResizeObserver }).ResizeObserver = class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    } as unknown as new () => ResizeObserver;
  }
}
