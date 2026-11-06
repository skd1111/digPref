/**
 * 入口路径回落契约（BUGFIX #194）。
 *
 * 安装包窗口 url 是 `eaide://localhost/index.html`（tauri.conf.json，磁盘 web/
 * 热替换链路），WebView 的 location.pathname = `/index.html`；react-router 的
 * index 路由只匹配精确 `/`，无匹配时默认 errorElement 渲染整屏
 * 「Unexpected Application Error! 404 Not Found」。
 * 本测试硬约束：入口路径与任意 SPA 深链都必须命中路由，不再整屏 404。
 */
import { describe, expect, it } from 'vitest';
import { matchRoutes } from 'react-router-dom';
import { routes } from '../src/router';

describe('router 入口路径回落（BUGFIX #194）', () => {
  it('/index.html 入口路径命中根布局，而非无匹配 404', () => {
    const matches = matchRoutes(routes, '/index.html');
    expect(matches, 'matchRoutes 不应为空 —— 空即 react-router 抛 404 Response').toBeTruthy();
    expect(matches?.[0]?.route.path).toBe('/');
  });

  it('未知 SPA 深链也回落到通配路由', () => {
    const matches = matchRoutes(routes, '/some/unknown/deep-link');
    expect(matches).toBeTruthy();
    const leaf = matches?.[matches.length - 1]?.route;
    expect(leaf?.path).toBe('*');
  });

  it('正常路由不受通配兜底影响：/ 与 /settings/models 仍精确命中', () => {
    const root = matchRoutes(routes, '/');
    expect(root?.[root.length - 1]?.route.index).toBe(true);
    const settings = matchRoutes(routes, '/settings/models');
    expect(settings?.[settings.length - 1]?.route.path).toBe('settings/models');
  });
});
