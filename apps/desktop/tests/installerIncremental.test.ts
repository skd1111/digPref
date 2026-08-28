/**
 * NSIS 安装器增量更新护栏（v2.109，2026-08-25）。
 *
 * 用户要求：覆盖安装时"没改过的东西不要覆盖"。落地方式为在
 * eaide-hooks.nsh 的 NSIS_HOOK_PREINSTALL 里把覆盖策略改为
 * `SetOverwrite ifdiff`（时间戳相同 → 跳过；更旧或更新 → 覆盖），
 * 并在 NSIS_HOOK_POSTINSTALL 恢复默认策略，防止泄漏。
 *
 * 运行时用户资产（安装目录下的 skills/ workspace/ *.db / config/*.json）
 * 本就不在安装器负载内，升级天然不触碰；本测试锁定覆盖策略本身。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const hooksPath = resolve(here, "../src-tauri/hooks/eaide-hooks.nsh");
const confPath = resolve(here, "../src-tauri/tauri.conf.json");

function macroBody(content: string, macroName: string): string {
  const start = content.indexOf(`!macro ${macroName}`);
  expect(start).toBeGreaterThanOrEqual(0);
  const end = content.indexOf("!macroend", start);
  expect(end).toBeGreaterThan(start);
  return content.slice(start, end);
}

describe("NSIS 增量安装护栏", () => {
  const content = readFileSync(hooksPath, "utf-8");

  it("PREINSTALL 设置 SetOverwrite ifdiff（未变化的随包文件跳过重写）", () => {
    expect(macroBody(content, "NSIS_HOOK_PREINSTALL")).toContain("SetOverwrite ifdiff");
  });

  it("POSTINSTALL 恢复默认覆盖策略（防 ifdiff 泄漏到后续步骤）", () => {
    expect(macroBody(content, "NSIS_HOOK_POSTINSTALL")).toContain("SetOverwrite on");
  });
});

/**
 * 随包嵌套目录护栏（BUGFIX #160，2026-08-27）。
 *
 * Tauri bundle.resources 的 map 形式下，glob 匹配中的文件会被展平到目标目录
 * （只保留文件名，不保留相对路径，官方文档明示），导致安装目录里
 * vendor/ppt-master/workflows/ 等子目录消失、同名文件互相覆盖，
 * SKILL.md 引导读 ${SKILL_DIR}/workflows/routing.md 直接 FileNotFoundError。
 * 嵌套目录必须用目录形式（"dir/": "target/"），bundler 才会递归保留结构。
 */
describe("Tauri resources 嵌套目录防展平护栏", () => {
  const conf = JSON.parse(readFileSync(confPath, "utf-8")) as {
    bundle?: { resources?: Record<string, string> | string[] };
  };
  const resources = conf.bundle?.resources;

  it("resources 为 map 形式且包含 ppt-master / python 条目", () => {
    expect(Array.isArray(resources)).toBe(false);
    const keys = Object.keys(resources ?? {});
    expect(keys.some((k) => k.includes("vendor/ppt-master"))).toBe(true);
    expect(keys.some((k) => k.includes("vendor/python"))).toBe(true);
  });

  it("嵌套随包目录不用 **/*（map + glob 会展平丢目录，BUGFIX #160）", () => {
    for (const src of Object.keys(resources ?? {})) {
      if (src.includes("vendor/ppt-master") || src.includes("vendor/python")) {
        expect(src).not.toContain("**");
        expect(src).not.toContain("*");
      }
    }
  });
});
