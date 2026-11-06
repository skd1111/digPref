/**
 * 前端磁盘加载护栏（runbook §6.1 第 5 步）。
 *
 * 背景：前端原本内嵌在 exe（frontendDist → include_bytes!），改前端必须重编 Rust。
 * 现在窗口走自定义协议 `eaide://localhost`，由 src-tauri/src/web_loader.rs 从磁盘
 * `<安装根>/web/`（versions/web/<active> 优先）读文件，因此只需 `pnpm build` +
 * 覆盖 `versions/web/<ver>/` 就能生效。
 *
 * 这几处一旦被改回去（url 变回相对路径、resources 少了 dist、协议没注册、
 * dev 分支丢失），热替换会静默失效，故全部锁进测试。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const confPath = resolve(here, "../src-tauri/tauri.conf.json");
const libPath = resolve(here, "../src-tauri/src/lib.rs");

type Conf = {
  app?: { windows?: Array<{ url?: string }> };
  bundle?: { resources?: Record<string, string> };
};

describe("前端磁盘加载护栏", () => {
  const conf = JSON.parse(readFileSync(confPath, "utf-8")) as Conf;
  const lib = readFileSync(libPath, "utf-8");

  it("主窗口 url 走 eaide 自定义协议且 path 为根（BUGFIX #194：带 index.html 后缀会让 react-router 整屏 404）", () => {
    const url = conf.app?.windows?.[0]?.url;
    expect(url).toBe("eaide://localhost/");
  });

  it("dist 以整目录形式映射到安装根 web/（不能用 ** glob，会拍平丢 assets/）", () => {
    const resources = conf.bundle?.resources ?? {};
    expect(resources["../dist"]).toBe("./web/");
    for (const src of Object.keys(resources)) {
      expect(src).not.toContain("**");
    }
  });

  it("lib.rs 注册了 eaide 异步协议并委托给 web_loader::serve", () => {
    expect(lib).toContain('register_asynchronous_uri_scheme_protocol("eaide"');
    expect(lib).toContain("web_loader::serve(");
  });

  it("dev 模式把窗口 url 改回 WebviewUrl::App（保住 devUrl + HMR）", () => {
    // CustomProtocol 不会被 devUrl 覆盖：少了这段 `pnpm tauri dev` 会白屏
    expect(lib).toContain("tauri::is_dev()");
    expect(lib).toContain('tauri::WebviewUrl::App("index.html".into())');
  });
});
