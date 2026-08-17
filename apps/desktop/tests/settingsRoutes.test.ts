/**
 * Settings 子路由注册一致性回归（BUGFIX #18/#19/#94/#99）。
 *
 * 历史上四次同类事故：SettingsView 加了新 Tab（SECTIONS/KNOWN_SEGS），
 * 但漏在 router.tsx 注册路径，点击后 react-router 抛 404 白屏。
 * 本测试把「KNOWN_SEGS 与 router.tsx 路径一一对应」变成编译/测试期硬约束。
 */
import { describe, expect, it } from 'vitest';
import type { RouteObject } from 'react-router-dom';
import { router } from '../src/router';

/** 与 SettingsView.tsx 的 KNOWN_SEGS 保持同步 */
const SETTINGS_SEGS = [
  'envs',
  'models',
  'gen-limits',
  'dspark',
  'secrets',
  'terminal',
  'about',
  'skills',
  'expert-teams',
  'router',
  'codenav',
  'toolchain',
  'workspace',
  'advanced',
];

describe('settings 子路由注册一致性', () => {
  it('每个 SettingsView 页签在 router.tsx 都有对应 settings/<seg> 路由', () => {
    const root = router.routes.find((r: RouteObject) => r.path === '/');
    expect(root).toBeTruthy();
    const paths = new Set((root?.children ?? []).map((c: RouteObject) => c.path ?? ''));
    for (const seg of SETTINGS_SEGS) {
      expect(paths.has(`settings/${seg}`), `router.tsx 缺少 settings/${seg} 路由`).toBe(true);
    }
  });
});
