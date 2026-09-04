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
 * 随包资源护栏。
 *
 * 2026-09-03：不再内置 ppt-master——vendor/ppt-master 整体移出 bundle.resources
 * （PPT 技能改由用户经 /skills/import 自行上传），安装包因此瘦身 ~100MB；
 * 内嵌 Python（vendor/python）更早已移除。本护栏锁定二者都不得回到 resources。
 *
 * 另保留 BUGFIX #160 教训：map 形式的 bundle.resources 下，`**` glob 匹配的文件
 * 会被展平到目标目录（只留文件名、丢相对路径），嵌套目录必须用目录形式（"dir/": "target/"）。
 */
describe("Tauri resources 随包护栏", () => {
  const conf = JSON.parse(readFileSync(confPath, "utf-8")) as {
    bundle?: { resources?: Record<string, string> | string[] };
  };
  const resources = conf.bundle?.resources;

  it("resources 为 map 形式，且不再捆绑 vendor/ppt-master 与 vendor/python", () => {
    expect(Array.isArray(resources)).toBe(false);
    const keys = Object.keys(resources ?? {});
    // 2026-09-03：ppt-master 不再内置（用户自行上传 PPT 技能）；内嵌 Python 早已移除
    expect(keys.some((k) => k.includes("vendor/ppt-master"))).toBe(false);
    expect(keys.some((k) => k.includes("vendor/python"))).toBe(false);
  });

  it("随包资源不用 ** 双星 glob（map + ** 会展平丢目录，BUGFIX #160）", () => {
    for (const src of Object.keys(resources ?? {})) {
      expect(src).not.toContain("**");
    }
  });
});
